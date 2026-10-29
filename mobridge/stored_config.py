"""Stored-config 'Rosetta stone': validate value encodings against the reference REAL
stored settings, with no changes to his setup.

He has two kinds of ground-truth already captured (no device interaction needed):
  - the device NVM/settings partition dump (`nvm_oct20.bin`, ~384 KB), and
  - the `.rax` config backups the app wrote.

These contain real stored parameter bytes. A value encoding (gain->raw, freq->
coeff, Q, slope, delay) is STORED_CONFIG-proven when:

  1. the decompiled decoder turns the stored bytes into sensible human values
     within the control's range, AND
  2. the decompiled encoder turns those values back into the exact stored bytes
     (round-trip), across all of his stored instances.

That gives many known (value, bytes) pairs for free. This module parses the
containers and runs the round-trip; the per-field formulas come from
`mobridge.encoders` once the decompile pins them.

STATUS: scaffolding. The `.rax` and NVM dumps are not in the cloud project yet
(they are in his recovery set on the laptop). Parsers below are structured for
them and tested on synthetic data; field offsets are filled from the decompile /
a sample file. Nothing here sends anything to a device.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from . import encoders, parity


@dataclass
class StoredValue:
    field: str          # logical name, e.g. 'eq_gain'
    raw: bytes          # the stored bytes
    decoded: float | None = None   # human value, once a decoder exists


class RaxFile:
    """A .rax config backup. Format not yet pinned; detect JSON vs binary and
    expose the raw blob plus any structure we can read without guessing values."""

    def __init__(self, data: bytes, path: str = '<bytes>'):
        self.data = data
        self.path = path
        self.json = None
        try:
            self.json = json.loads(data.decode('utf-8', 'strict'))
        except (ValueError, UnicodeDecodeError):
            self.json = None   # binary container; needs the decompiled schema

    @classmethod
    def load(cls, path: str) -> 'RaxFile':
        with open(path, 'rb') as f:
            return cls(f.read(), path)

    def fields(self) -> list[StoredValue]:
        # TODO(decompile/sample): map container offsets -> logical fields.
        return []


class NvmDump:
    """The device NVM/settings partition (0x60000 region export)."""

    def __init__(self, data: bytes, path: str = '<bytes>'):
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: str) -> 'NvmDump':
        with open(path, 'rb') as f:
            return cls(f.read(), path)

    def read_field(self, offset: int, fmt: str) -> tuple:
        return struct.unpack_from(fmt, self.data, offset)


def roundtrip_ok(field: str, raw: bytes) -> bool:
    """A field's encoding is self-consistent on real data when decode->encode
    returns the original bytes. Requires a confirmed encoder (else raises)."""
    enc = encoders.get(field)
    # decode is the encoder's inverse; both come from the decompile.
    if not enc.confirmed or not hasattr(enc, 'decode'):
        raise encoders.EncodingUnconfirmed(
            'no confirmed codec for %r yet; cannot validate against stored config' % field)
    value = enc.decode(raw)              # type: ignore[attr-defined]
    return enc.encode(value) == raw


def validate(fields: list[StoredValue]) -> dict:
    """Run the round-trip over stored values; report which fields are proven.
    Fields with a confirmed, self-consistent codec reach STORED_CONFIG."""
    result = {'proven': [], 'blocked': [], 'status': {}}
    for sv in fields:
        try:
            ok = roundtrip_ok(sv.field, sv.raw)
        except encoders.EncodingUnconfirmed:
            result['blocked'].append(sv.field)
            result['status'][sv.field] = parity.Status.UNCONFIRMED.name
            continue
        if ok:
            result['proven'].append(sv.field)
            result['status'][sv.field] = parity.Status.STORED_CONFIG.name
        else:
            result['blocked'].append(sv.field)
            result['status'][sv.field] = parity.Status.UNCONFIRMED.name
    return result


# --- real dump ingestion -------------------------------------------------------

NVM_MAGIC = bytes([0x5f, 0xb5, 0xf0, 0x17])   # README: NVM/settings header magic

# DSPNV settings version (device-config [Confirmed], verified vs the reference dump
# 2026-10-21). In a settings-PARTITION dump that starts at flash 0x60000:
#   u32 LE @ 0xA10 = the DSP Parameters file version (file offset 0x0) -- reference unit = 210
#   u32 LE @ 0xA0C = zlib.crc32 over data[0xA10 : 0xA10+0x1BB14]
# The firmware rewrites the DSP defaults if the version is unexpected, so the CRC
# must verify before the version is trusted. This version drives the gain-unity
# gate (see encoders.set_gain_unity_for_fw: >207 -> 0xED00, else 0xE700).
DSPNV_VERSION_OFF = 0xA10
DSPNV_CRC_OFF = 0xA0C
DSPNV_CRC_LEN = 0x1BB14


def dspnv_version(data: bytes, base: int = 0) -> dict | None:
    """Read the DSPNV settings version and verify its CRC from a settings-partition
    dump (one that starts at flash 0x60000). `base` shifts the offsets if the dump
    starts elsewhere. Returns {'version', 'crc_ok', 'stored_crc', 'computed_crc'} or
    None if the buffer is too short. Only trust 'version' when crc_ok is True."""
    import struct
    import zlib
    vo, co = base + DSPNV_VERSION_OFF, base + DSPNV_CRC_OFF
    end = vo + DSPNV_CRC_LEN
    if len(data) < end or co + 4 > len(data):
        return None
    stored = struct.unpack_from('<I', data, co)[0]
    computed = zlib.crc32(data[vo:end]) & 0xFFFFFFFF
    version = struct.unpack_from('<I', data, vo)[0]
    return {'version': version, 'crc_ok': stored == computed,
            'stored_crc': stored, 'computed_crc': computed}


def scan_regions(data: bytes, gap: int = 16) -> list[tuple[int, int]]:
    """Non-0xFF (written) spans, merging runs separated by < `gap` FF bytes.
    Lets us see where the real stored settings are without guessing their schema
    (the NVM field map itself is owned by the Device-config thread / DEVICE_CONFIG.md)."""
    out = []
    i, n = 0, len(data)
    ff = bytes([0xFF]) * gap
    while i < n:
        if data[i] != 0xFF:
            j = i
            while j < n and data[j:j + gap] != ff:
                j += 1
            out.append((i, j)); i = j
        else:
            i += 1
    return out


def nvm_summary(nvm: 'NvmDump') -> dict:
    d = nvm.data
    regions = scan_regions(d)
    written = sum(b - a for a, b in regions)
    return {
        'size': len(d),
        'has_magic': d[:4] == NVM_MAGIC,
        'written_bytes': written,
        'written_pct': round(100 * written / len(d), 1) if d else 0,
        'regions': regions,
    }


def load_dumps(dir_path: str) -> dict:
    """Load whatever device dumps are present in a directory (the unzipped
    device-dumps). Returns {'nvm': NvmDump|None, 'rax': [RaxFile], 'app': bytes|None}."""
    import glob
    import os
    out = {'nvm': None, 'rax': [], 'app': None}
    for fn in sorted(glob.glob(os.path.join(dir_path, '*'))):
        low = fn.lower()
        if low.endswith('.rax'):
            out['rax'].append(RaxFile.load(fn))
        elif 'settings' in low and low.endswith('.bin'):
            out['nvm'] = NvmDump.load(fn)
        elif 'appimage' in low and low.endswith('.bin'):
            out['app'] = open(fn, 'rb').read()
    return out


# --- candidate-formula corroboration against the NVM field map -----------------
# This does NOT clear the send-gate. It checks whether a CANDIDATE encoder (see
# encoders.*.candidate) reproduces the reference real stored bytes, so a formula earns
# confidence from his own data before anyone trusts it. Sending stays blocked
# until the formula's reference is independently anchored and `confirmed` flips.

def load_field_map(path: str) -> list[dict]:
    with open(path, encoding='utf-8') as f:
        return json.load(f)['fields']


def corroborate(field_map_path: str, field_suffix: str, encoder: str,
                ignore_raw: set[bytes] | None = None) -> dict:
    """For every field whose name ends with `field_suffix` (e.g. '.gain'), decode
    its stored bytes with the candidate codec and re-encode; count exact byte
    matches. `ignore_raw` skips sentinel/default values (e.g. b'\\x00\\x00' for an
    unused channel). Returns residual stats + the distinct (value,bytes) pairs
    his data provides for free."""
    enc = encoders.ENCODERS[encoder]
    ignore_raw = ignore_raw or set()
    fields = load_field_map(field_map_path)
    total = matched = skipped = 0
    pairs: dict[float, str] = {}
    mismatches = []
    for x in fields:
        if not x['name'].endswith(field_suffix):
            continue
        raw = bytes.fromhex(x['device_raw'])
        if raw in ignore_raw:
            skipped += 1
            continue
        total += 1
        value = enc.decode(raw)
        back = enc.encode_candidate(value)
        if back == raw:
            matched += 1
            pairs[round(value, 3)] = raw.hex()
        else:
            mismatches.append((x['name'], raw.hex(), round(value, 3), back.hex()))
    return {
        'encoder': encoder,
        'candidate': enc.candidate,
        'confirmed': enc.confirmed,
        'fields_checked': total,
        'exact_matches': matched,
        'skipped_sentinel': skipped,
        'distinct_pairs': dict(sorted(pairs.items())),
        'mismatches': mismatches,
        'clears_gate': False,   # corroboration only; never clears sending
    }


# --- .rax legacy gain offset — FILE IMPORT ONLY, never the live device path ------
# PARITY TRAP (coordinator, laptop-confirmed 2026-10-21): the app applies a one-time
# -3.0 dB gain offset ONLY in the .rax FILE parser, gated by
#   (model_version > 207) AND (file_version < 208)
# i.e. it fires only when importing an OLD (pre-v208) preset file into a new model,
# to re-reference its gains to the new 0xED00 zero point. It is NOT part of the
# live/device decode (that is handled entirely by the version-gated unity in
# encoders.set_gain_unity_for_fw). NEVER call this on a value read from the device.
RAX_LEGACY_GAIN_OFFSET_DB = -3.0


def rax_legacy_gain_offset(model_version: int, file_version: int) -> float:
    """dB offset to add to a gain value read FROM A .rax FILE during import.
    Returns -3.0 only when importing a pre-v208 file into a >v207 model, else 0.0.
    File-import path only — must never touch a live device value."""
    if model_version > 207 and file_version < 208:
        return RAX_LEGACY_GAIN_OFFSET_DB
    return 0.0


# --- settings file backup (the official app's "Save" = .rax export to PC) -------
# The official app's Save writes a .rax config backup to the PC; it is NOT a
# device command. Exporting a file transmits nothing to the amp, so it is not
# parity-gated. The .rax BINARY layout is not pinned yet (RaxFile above), so this
# writes an interim JSON backup with a clear marker; swap to the real .rax packer
# once the format is decoded, keeping the same call.
BACKUP_MARKER = 'custom-mobridge-settings-backup'


def export_backup(path: str, settings: dict, meta: dict | None = None) -> str:
    doc = {
        '_format': BACKUP_MARKER,
        '_note': 'Interim JSON backup. Real .rax binary layout pending decode; '
                 'contents are the settings known to Custom Mobridge at save time.',
        'meta': meta or {},
        'settings': settings,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    return path
