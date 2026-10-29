"""Every MDI command the official app (mObridge DSP v25.8.0) can build, and every
reply it parses, as byte-exact builders/parsers.

Source: re/analysis/CMD_TABLE.md (§1 general, §2 CAN/0x06xx, §3 DSP),
FLASH_JOBS_REGION.md §4/§5 (flash + file ops), FW_LOCKED_WRITES_OCT22.md, and the
2026-10-22 wire captures for the connect sequence. Each entry records where its
bytes come from and what the decompile left UNRESOLVED; nothing here is guessed.

This module is the *spec*: it does not decide what may be SENT. parity.py keeps
that decision (the user's gate), dspwrite.py drives the DSP sync from the model,
and the job runner clears every frame through parity.assert_frame_cleared.

Layout conventions (TRANSPORT_REGION §2.1): all integers big-endian, f32/f64
IEEE big-endian, "half" = binary16 big-endian, raw = fixed-length byte block.
`fw` is the numeric firmware version from 0x0714 sub 0 (reference unit: 210 = 0xD2);
`presets` is dev+0x46 (preset count from 0x0716 sub 2; 0 = legacy no-preset
device) which decides whether the preset byte is sent.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .eq import _point as _eq_point        # f32 freq + 3 x half (proven [CAP])

# --------------------------------------------------------------------------
# primitives (TRANSPORT_REGION §2.1 builders FUN_14016eeb0 .. FUN_14016ecf0)
# --------------------------------------------------------------------------
u8 = lambda v: bytes([v & 0xFF])
u16 = lambda v: (v & 0xFFFF).to_bytes(2, 'big')
u32 = lambda v: (v & 0xFFFFFFFF).to_bytes(4, 'big')
u64 = lambda v: (v & 0xFFFFFFFFFFFFFFFF).to_bytes(8, 'big')
f32 = lambda v: struct.pack('>f', float(v))
f64 = lambda v: struct.pack('>d', float(v))
half = lambda v: struct.pack('>e', float(v))


def raw(b: bytes, n: int) -> bytes:
    b = bytes(b)[:n]
    return b + b'\0' * (n - len(b))


class _Reader:
    """Sequential reply reader mirroring FUN_14016e960/eb40/ebb0 etc."""
    def __init__(self, d: bytes):
        self.d, self.i = bytes(d), 0

    def left(self) -> int:
        return len(self.d) - self.i

    def _take(self, n):
        if self.i + n > len(self.d):
            raise ValueError('reply too short')
        v = self.d[self.i:self.i + n]
        self.i += n
        return v

    def u8(self): return self._take(1)[0]
    def u16(self): return int.from_bytes(self._take(2), 'big')
    def u32(self): return int.from_bytes(self._take(4), 'big')
    def u64(self): return int.from_bytes(self._take(8), 'big')
    def f32(self): return struct.unpack('>f', self._take(4))[0]
    def f64(self): return struct.unpack('>d', self._take(8))[0]
    def half(self): return struct.unpack('>e', self._take(2))[0]
    def raw(self, n): return self._take(n)


# --------------------------------------------------------------------------
# capability bits at sess+0x38 (CMD_TABLE §5) and firmware thresholds
# --------------------------------------------------------------------------
CAP = {
    'upmix0': 0x1, 'localid_table': 0x2, 'upmix2': 0x4, 'timealign': 0x8, 'headunit': 0x10,
    'master_gain': 0x10000, 'channel': 0x20000, 'preset': 0x40000, 'routing': 0x80000,
    'sourcesel': 0x100000, 'bins_channel': 0x200000, 'biquad': 0x400000, 'bins_master': 0x800000,
    'input_gains': 0x1000000, 'input_delay': 0x2000000, 'name': 0x4000000, 'fwver': 0x8000000,
    'chanprops': 0x10000000, 'chanpoints': 0x20000000, 'filter': 0x40000000, 'masterpoints': 0x80000000,
}


@dataclass(frozen=True)
class Spec:
    """One command the exe can transmit (or one unsolicited reply it parses)."""
    cmd: int
    name: str
    sub: int | None = None
    direction: str = 'tx'          # tx | rx-only
    gate: int = 0                  # sess+0x38 capability bit (0 = none)
    fw_min: int = 0                # extra firmware threshold
    session_type: int | None = None  # 0x06xx commands: sess+0 type code
    source: str = ''
    unresolved: str = ''           # what the decompile could not pin (empty = fully pinned)
    captured: bool = False         # seen on the reference wire


SPECS: list[Spec] = []


def _spec(*a, **k):
    s = Spec(*a, **k)
    SPECS.append(s)
    return s


# ==========================================================================
# §1 general / system
# ==========================================================================
_spec(0x0000, 'Keepalive', source='capture 2026-10-22 (empty frame, connect sequence); outside the decompiled region', captured=True)
_spec(0x0001, 'Identify', source='CMD_TABLE §1.1', captured=True)
_spec(0x0002, 'SetBaud', source='capture 2026-10-22 (00 03 84 00 = 230400); outside the decompiled region', captured=True)
_spec(0x0020, 'ListQuery', source='§1.2', unresolved='content of the byte list', captured=True)
_spec(0x0025, 'InfoBlock', source='§1.3', captured=True)
_spec(0x0100, 'LocalIDRead', gate=0x2, source='§1.4 + FW_LOCKED_WRITES §4 (LocalIDRead; dead on 2.11.x: 0x0fff)')
_spec(0x0101, 'LocalIDWrite', gate=0x2, source='§1.4 + FW_LOCKED_WRITES §4 (LocalIDWrite; dead on 2.11.x: 0x0fff)')
_spec(0x0202, 'Block0202', source='§1.8', unresolved='field meanings (u8 + 2 x u32 from the session)')
_spec(0x0203, 'Block0203', source='§1.8', unresolved='field meanings')
_spec(0x0282, 'ReceiveFileAck', source='§1.7', captured=True)
_spec(0x0302, 'FilePut', source='§1.7 / FLASH §5.4', unresolved='layout of the 0x100-byte request descriptor')
_spec(0x0303, 'DirList', source='§1.7 / FLASH §5.4')
_spec(0x0304, 'FileSelect', source='§1.7 / FLASH §5.4', unresolved='descriptor layout; side effect (open/delete)')
_spec(0x0305, 'FileReadBlock', source='§1.7 / FLASH §5.4', unresolved='descriptor layout')
_spec(0x0400, 'StreamFlag', source='§1.5', captured=True)
_spec(0x0401, 'DebugSend', source='§1.6', captured=True)
_spec(0x0010, 'Reset', source='FLASH §4 + JOB_TABLE_AND_FLASH §4 (job 12; reboot; also ends a flash)')
_spec(0x0011, 'FactoryReset', source='FLASH §4 + JOB_TABLE_AND_FLASH §4 (job 13; NV re-init + reboot)')
_spec(0x0201, 'SendFile', source='FLASH §4')
_spec(0x0281, 'ReceiveFile', source='FLASH §4')


def identify() -> bytes: return b''
def set_baud(baud: int = 230400) -> bytes: return u32(baud)
def list_query() -> bytes: return b''
def info_block() -> bytes: return b''
def localid_read() -> bytes: return u32(2)                       # key 2
def localid_write(node_id: int) -> bytes: return u32(2) + u32(node_id & 0xFF)   # *(u8*)(dev+1) widened
def block_0202(a: int, b: int, c: int) -> bytes: return u8(a) + u32(b) + u32(c)
block_0203 = block_0202
def receive_file_ack() -> bytes: return b''
def file_put(descriptor: bytes) -> bytes: return raw(descriptor, 0x100)
def dir_list() -> bytes: return b''
def file_select(descriptor: bytes) -> bytes: return raw(descriptor, 0x100)
def file_read_block(descriptor: bytes) -> bytes: return raw(descriptor, 0x100)
def stream_flag(on: bool) -> bytes: return u8(1 if on else 0)
def debug_send(text: str) -> bytes: return text.encode('latin-1')  # one byte per char, no terminator
def flash_begin() -> bytes: return b''
def flash_commit() -> bytes: return b''
def send_file() -> bytes: return b''
def receive_file() -> bytes: return b''


def parse_identify(d: bytes) -> dict:
    """Up to four {id u16, a u8, major, minor, patch} groups (§1.1)."""
    r, groups = _Reader(d), []
    while r.left() >= 6 and len(groups) < 4:
        groups.append({'id': r.u16(), 'a': r.u8(), 'major': r.u8(), 'minor': r.u8(), 'patch': r.u8()})
    return {'groups': groups}


def parse_list_query(d: bytes) -> dict:
    r = _Reader(d); n = r.u32()
    return {'bytes': r.raw(min(n, r.left()))}


def parse_info_block(d: bytes) -> dict:
    r = _Reader(d)
    return {'b0': r.u8(), 'b1': r.u8(), 'w0': r.u32(), 'w1': r.u32(), 'blob': r.raw(0x40)}


def parse_localid_read(d: bytes) -> dict:
    r = _Reader(d); key = r.u32()
    if key != 2:
        raise ValueError('LocalIDRead key %d != 2' % key)
    return {'node_id': r.u32() & 0xFF}


def parse_dir_list(d: bytes) -> dict:
    r = _Reader(d); n = r.u16(); out = []
    for _ in range(n):
        out.append({'index': r.u16(), 'name': r.raw(0x100).split(b'\0')[0].decode('latin-1'), 'size': r.u32()})
    return {'entries': out}


def parse_file_block(d: bytes) -> dict:          # 0x0302 / 0x0305
    return {'block': _Reader(d).raw(0x100)}


def parse_result_u8(d: bytes) -> dict:           # 0x0282 / 0x0201 / 0x0280 : 1 = success
    return {'result': _Reader(d).u8()}


def parse_flash_progress(d: bytes) -> dict:
    """0x0200 {sub, u32 arg} -> FUN_14017a8c0 phase table (FLASH §4)."""
    r = _Reader(d); sub = r.u8() if len(d) == 5 else r.u16(); arg = r.u32()
    phase = {0: 1, 1: 2, 2: 3, 3: 4, 4: 7, 5: 5, 6: 'size', 7: 'position', 8: 'complete', 9: 'error',
             10: 8, 11: 9, 12: 10}.get(sub)
    return {'sub': sub, 'arg': arg, 'phase': phase}


def parse_error(d: bytes) -> dict:               # 0x0fff: u16 code (only 0x0011 inspects it, == 2)
    return {'code': _Reader(d).u16() if len(d) >= 2 else None}


# ==========================================================================
# §2 CAN / MOST / A2B session commands (0x06xx), payloads from the session struct
# ==========================================================================
_spec(0x0610, 'Bus0610', session_type=0x25, source='§2', unresolved='field meanings')
_spec(0x0612, 'BusOpen', session_type=0x24, source='§2 (u32 arg; also sess 0x26 variant)')
_spec(0x0620, 'Bus0620', session_type=0x27, source='§2')
_spec(0x0621, 'BusMsgSend', session_type=0x28, source='§2 (+ dispatcher 0x0621 -> FUN_140172ec0 not in file)',
      unresolved='reply handler body')
_spec(0x0624, 'Bus0624', session_type=0x2a, source='§2')
_spec(0x0625, 'Bus0625', session_type=0x29, source='§2')
_spec(0x0644, 'Bus0644', session_type=0x2c, source='§2')
_spec(0x0645, 'Bus0645', session_type=0x2d, source='§2')
_spec(0x0649, 'BusQuery', session_type=0x2e, source='§2 (type 1/3/4/5 replies)')
_spec(0x064a, 'BusI2C', source='§2 (a2b.i2c shape: u16 addr, u8 reg, u8 n, n bytes)')
_spec(0x064b, 'Bus064b', source='§2')
_spec(0x064c, 'Bus064c', session_type=0x31, source='§2')
_spec(0x0650, 'BusRaw', session_type=0x2b, source='§2')


def bus_0610(w: int, b: int, dw: int, data: bytes) -> bytes:
    return u16(w) + u8(b) + u32(dw) + bytes(data[:8])


def bus_open(arg: int) -> bytes: return u32(arg)
def bus_0620() -> bytes: return b''


def bus_msg_send(opt_u16: int, b32: int, b33: int, w: int, b36: int, data: bytes) -> bytes:
    out = u16(opt_u16) if opt_u16 else b''
    return out + u8(b32) + u8(b33) + u16(w) + u8(b36) + u8(0) + u16(len(data)) + bytes(data)


def bus_0624(value: int | None) -> bytes: return b'' if value is None else u8(value)
def bus_0625() -> bytes: return b''
def bus_0644() -> bytes: return b''
def bus_0645(w: int, b: int, data: bytes) -> bytes: return u16(w) + u8(b) + u16(len(data)) + bytes(data)
def bus_query(kind: int) -> bytes: return u8(kind)
def bus_i2c(addr: int, reg: int, n: int, data: bytes) -> bytes: return u16(addr) + u8(reg) + u8(n) + bytes(data[:len(data)])
def bus_064b() -> bytes: return b''
def bus_064c(b: int) -> bytes: return u8(b)
def bus_raw(b: int, data: bytes) -> bytes: return u8(b) + bytes(data)


def parse_bus_query(d: bytes) -> dict:
    r = _Reader(d); t = r.u8(); out = {'type': t}
    if t == 3:
        out.update(b36=r.u8(), b37=r.u8())
    elif t == 4:
        out.update(b36=r.u8(), w=r.u16())
    elif t == 5:
        out.update(w36=r.u16())
    elif t != 1:
        return out
    out['status'] = r.u8()
    return out


# unsolicited (dispatcher FUN_140173220, CMD_TABLE §2/§4)
_spec(0x0615, 'Event0615', direction='rx-only', source='§2 dispatcher (event 8)')
_spec(0x0646, 'Event0646', direction='rx-only', source='§2 dispatcher (event 0xc)')
_spec(0x0651, 'Event0651', direction='rx-only', source='§2 dispatcher (event 10)')
_spec(0x070e, 'EventTable070e', direction='rx-only', source='§2 dispatcher (event 0xd)')
_spec(0x0716, 'PresetChanged', sub=3, direction='rx-only', source='§2 dispatcher (event 0xb)')
_spec(0x0720, 'Event0720', direction='rx-only', source='§2 dispatcher (event 0xe)')
_spec(0x0fff, 'Error', direction='rx-only', source='FLASH §8')


def parse_event_0615(d: bytes) -> dict:
    r = _Reader(d); out = {'dw18': r.u32(), 'w28': r.u16(), 'b14': r.u8(), 'dw20': r.u32()}
    out['data'] = r.raw(min(8, r.left()))
    return out


def parse_event_0646(d: bytes) -> dict:
    r = _Reader(d); s = r.u8(); out = {'sub': s}
    if s == 0:
        out.update(w14=r.u16(), b16=r.u8()); n = r.u16(); out['data'] = r.raw(n)
    elif s == 3:
        out.update(b14=r.u8(), b16=r.u8())
    elif s == 4:
        out.update(b14=r.u8(), w16=r.u16())
    elif s == 5:
        out.update(w14=r.u16())
    return out


def parse_event_0651(d: bytes) -> dict:
    r = _Reader(d); return {'b10': r.u8(), 'data': r.raw(r.left())}


def parse_event_070e(d: bytes) -> dict:
    r = _Reader(d); t = r.u8(); out = {'type': t}
    if t == 1:
        out['slot'] = r.u8(); out['table'] = [r.u16() for _ in range(0x400)]
    return out


def parse_preset_changed(d: bytes) -> dict | None:
    return {'preset': d[1]} if len(d) == 2 and d[0] == 3 else None


def parse_event_0720(d: bytes) -> dict: return {'w': _Reader(d).u16()}


# ==========================================================================
# §3 DSP reads 0x070f-0x0719 and writes 0x0700-0x0708
# ==========================================================================
_spec(0x0714, 'ReadFwVersion', sub=0, gate=CAP['fwver'], source='§3.1', captured=True)
_spec(0x0714, 'ReadName', sub=1, gate=CAP['name'], source='§3.2', captured=True)
_spec(0x0714, 'ReadChanProps', sub=2, gate=CAP['chanprops'], source='§3.8', captured=True)
_spec(0x0714, 'ReadFilter', sub=3, gate=CAP['filter'], source='§3.9', captured=True)
_spec(0x0714, 'ReadChanPoints', sub=5, gate=CAP['chanpoints'], source='§3.10', captured=True)
_spec(0x0714, 'ReadMasterPoints', sub=6, gate=CAP['masterpoints'], source='§3.11', captured=True)
_spec(0x0714, 'ReadInputGains', sub=7, gate=CAP['input_gains'], source='§3.12', captured=True)
_spec(0x0714, 'ReadInputDelay', sub=8, gate=CAP['input_delay'], source='§3.13', captured=True)
_spec(0x0716, 'SelectPreset', sub=1, gate=CAP['preset'], source='§3.3')
_spec(0x0716, 'ReadPresetCount', sub=2, gate=CAP['preset'], source='§3.3', captured=True)
_spec(0x0716, 'ReadCurrentPreset', sub=3, gate=CAP['preset'], source='§3.3', captured=True)
_spec(0x0713, 'ReadSourceSel', gate=CAP['sourcesel'], source='§3.4', captured=True)
_spec(0x0703, 'WriteSourceSel', gate=CAP['sourcesel'], source='§3.4 + FW_LOCKED_WRITES §2')
_spec(0x0711, 'ReadRouting', gate=CAP['routing'], source='§3.5', captured=True)
_spec(0x0701, 'WriteRouting', gate=CAP['routing'], source='§3.5')
_spec(0x0710, 'ReadMaster', sub=0, gate=CAP['master_gain'], source='§3.6', captured=True)
_spec(0x0700, 'WriteMaster', sub=0, gate=CAP['master_gain'], source='§3.6', captured=True)
_spec(0x0710, 'ReadChannel', gate=CAP['channel'], source='§3.7', captured=True)
_spec(0x0700, 'WriteChannel', gate=CAP['channel'], source='§3.7 + EXE_DEEP_RE', captured=True)
_spec(0x0704, 'WriteName', sub=1, gate=CAP['name'], source='§3.2')
_spec(0x0704, 'WriteChanProps', sub=2, gate=CAP['chanprops'], source='§3.8', captured=True)
_spec(0x0704, 'WriteFilter', sub=3, gate=CAP['filter'], source='§3.9', captured=True,
      unresolved='fw 201/202 only: five trailing f32 (value lost by the decompiler)')
_spec(0x0704, 'WriteChanPoints', sub=5, gate=CAP['chanpoints'], source='§3.10', captured=True)
_spec(0x0704, 'WriteMasterPoints', sub=6, gate=CAP['masterpoints'], source='§3.11', captured=True)
_spec(0x0704, 'WriteInputGains', sub=7, gate=CAP['input_gains'], source='§3.12 + .rdata reads + FW §5')
_spec(0x0704, 'WriteInputDelay', sub=8, gate=CAP['input_delay'], source='§3.13')
_spec(0x0702, 'WriteBinsChannel', gate=CAP['bins_channel'], source='§3.14 + filterbins sweep', captured=True)
_spec(0x0702, 'WriteBinsMaster', gate=CAP['bins_master'], source='§3.14', captured=True)
_spec(0x0708, 'WriteBiquads', gate=CAP['biquad'], fw_min=0xCB, source='§3.15', captured=True)
_spec(0x0705, 'WriteHeadUnit', gate=CAP['headunit'], source='§3.16 + FW_LOCKED_WRITES §1')
_spec(0x0706, 'ReadHeadUnit', gate=CAP['headunit'], source='§3.16')
_spec(0x0707, 'WriteUpmix0', sub=0, gate=CAP['upmix0'], source='§3.17', unresolved='field meanings of the 0x24-byte record')
_spec(0x0707, 'WriteTable', sub=1, gate=CAP['localid_table'], source='§3.18', unresolved='table meaning (u16[0x400] x 2 slots)')
_spec(0x0707, 'WriteUpmix', sub=2, gate=CAP['upmix2'], source='§3.19')
_spec(0x0707, 'WriteTimeAlign', sub=3, gate=CAP['timealign'], fw_min=0xD0, source='§3.20 + .rdata DAT_1401fdd28')
_spec(0x070f, 'ReadUpmix0', sub=0, gate=CAP['upmix0'], source='§3.17')
_spec(0x070f, 'ReadTable', sub=1, gate=CAP['localid_table'], source='§3.18')
_spec(0x070f, 'ReadUpmix', sub=2, gate=CAP['upmix2'], source='§3.19')
_spec(0x070f, 'ReadTimeAlign', sub=3, gate=CAP['timealign'], fw_min=0xD0, source='§3.20')
_spec(0x0717, 'DspParamWrite', source='§3.21 + DSP_FIELD_MEANINGS item 10 (u16 param id + u32 value)')
_spec(0x0718, 'DspParamRead', source='§3.21 + DSP_FIELD_MEANINGS item 10 (u16 param id -> u32 value)')
_spec(0x0719, 'BlockRead', source='§3.21', unresolved='type ids')


def _p(presets: int, preset: int) -> bytes:
    """The optional preset byte: sent only when dev+0x46 (preset count) != 0."""
    return u8(preset) if presets else b''


# --- reads --------------------------------------------------------------
def read_fw_version() -> bytes: return u8(0)
def read_name() -> bytes: return u8(1)
def read_chan_props(channel: int, presets: int, preset: int = 0) -> bytes: return u8(2) + _p(presets, preset) + u8(channel + 1)
def read_filter(channel: int, presets: int, preset: int = 0) -> bytes: return u8(3) + _p(presets, preset) + u8(channel + 1)
def read_chan_points(channel: int, presets: int, preset: int = 0) -> bytes: return u8(5) + _p(presets, preset) + u8(channel + 1)
def read_master_points(presets: int, preset: int = 0) -> bytes: return u8(6) + _p(presets, preset)
def read_input_gains(presets: int, preset: int = 0) -> bytes: return u8(7) + _p(presets, preset)
def read_input_delay(presets: int, preset: int = 0) -> bytes: return u8(8) + _p(presets, preset)
def read_preset_count() -> bytes: return u8(2)
def read_current_preset() -> bytes: return u8(3)
def select_preset(preset: int) -> bytes: return u8(1) + u8(preset)
def read_sourcesel() -> bytes: return b''
def read_routing(presets: int) -> bytes: return u8(0) if presets else b''
def read_master(presets: int, preset: int = 0) -> bytes: return _p(presets, preset) + u8(0)
def read_channel(channel: int, presets: int, preset: int = 0) -> bytes: return _p(presets, preset) + u8(channel + 1)
def read_head_unit(index: int) -> bytes: return u8(index)      # 0x0706 [index][value]: value byte echoes the table
def read_upmix0(fw: int, presets: int, preset: int = 0) -> bytes: return u8(0) + (u8(preset) if presets and fw > 0xD0 else b'')
def read_table(slot: int) -> bytes: return u8(1) + u8(slot)
def read_upmix(fw: int, presets: int, preset: int = 0) -> bytes: return u8(2) + (u8(preset) if presets and fw > 0xD0 else b'')
def read_timealign(fw: int, presets: int, preset: int = 0) -> bytes: return u8(3) + (u8(preset) if presets and fw >= 0xD1 else b'')
def block_0718(w: int) -> bytes: return u16(w)
def block_0717(w: int, dw: int) -> bytes: return u16(w) + u32(dw)
def block_read(kind: int, ident: int) -> bytes: return u8(kind) + (u8(ident) if kind in (6, 7) else u64(ident))


# --- writes (DSP) ---------------------------------------------------------
def write_name(name: bytes) -> bytes: return u8(1) + raw(name, 0x40)


def write_sourcesel(value: int) -> bytes: return u8(value)


def write_routing(input_ids: list[int], fw: int, presets: int) -> bytes:
    out = u8(0) if presets else b''
    for v in input_ids:
        out += u64(v) if fw >= 0xCD else u8(v)
    return out


def write_master(gain_raw: int, fw: int, presets: int, preset: int = 0, legacy=(0, 0, 0, 0)) -> bytes:
    out = _p(presets, preset) + u8(0) + (u16(gain_raw) if fw >= 0xCA else u8(gain_raw >> 8))
    if fw == 100:
        out += u8(0) + u16(0) + u8(0) + u8(0) + u16(0) + u8(0) + bytes(legacy)
    return out


def write_channel(channel: int, gain_raw: int, invert: bool, delay_us: int, passthru: int, mute: bool,
                  testtone: int, testtone_type: int, limiter_raw: int, fw: int, presets: int,
                  preset: int = 0) -> bytes:
    out = _p(presets, preset) + u8(channel + 1)
    out += u16(gain_raw) if fw >= 0xCA else u8(gain_raw >> 8)
    out += u8(invert) + u16(delay_us) + u8(passthru & (1 if fw < 0xD1 else 0xFF)) + u8(mute)
    out += u16(testtone) + u8(testtone_type)
    if fw > 0xCD:
        out += u32(limiter_raw)
    return out


def write_chan_props(channel: int, ordinal: int, xoverlink: int, fw: int, presets: int, preset: int = 0) -> bytes:
    out = u8(2) + _p(presets, preset) + u8(channel + 1) + u8(ordinal)
    if fw < 200:
        out += u8(0)
    out += u8(xoverlink)
    if fw < 200:
        out += f64(0.0) + u8(0) + u8(0) + u16(0) + u8(0)
    return out


def write_filter(channel: int, ftype: int, design: int, f1: int, f2: int, s1: int, s2: int, fw: int,
                 presets: int, preset: int = 0) -> bytes:
    out = u8(3) + _p(presets, preset) + u8(channel + 1) + u8(ftype) + u8(design)
    if fw < 200:
        return out + f64(f1) + f64(f2) + f64(s1) + f64(s2)
    out += u16(f1) + u16(f2) + u8(s1) + u8(s2)
    if fw in (0xC9, 0xCA):
        out += f32(0) * 5          # UNRESOLVED value (decompiler lost it); 0 assumed, never sent to fw 210
    return out


def write_chan_points(channel: int, points, fw: int, presets: int, preset: int = 0) -> bytes:
    out = u8(5) + _p(presets, preset) + u8(channel + 1)
    for i, pt in enumerate(points):
        if fw < 200:
            out += u8(i) + b''.join(f64(x) for x in (pt[0], pt[1], pt[2], pt[3] if len(pt) > 3 else pt[2]))
        else:
            out += _eq_point(pt)
    return out


def write_master_points(points, fw: int, presets: int, preset: int = 0) -> bytes:
    out = u8(6) + _p(presets, preset) + (f64(0.0) if fw < 200 else b'')
    for i, pt in enumerate(points):
        if fw < 200:
            out += u8(i) + b''.join(f64(x) for x in (pt[0], pt[1], pt[2], pt[3] if len(pt) > 3 else pt[2]))
        else:
            out += _eq_point(pt)
    return out


def write_input_gains(raws: list[int], fw: int, preset: int) -> bytes:
    """07 | preset (always) | N entries; N = 64 (fw > 203) else 8; u16 (fw >= 202) else hi byte."""
    n = 0x40 if fw > 0xCB else 8
    out = u8(7) + u8(preset)
    for v in list(raws)[:n] + [0xED00] * max(0, n - len(raws)):
        out += u16(v) if fw >= 0xCA else u8(v >> 8)
    return out


def write_input_delay(us: int, preset: int) -> bytes: return u8(8) + u8(preset) + u16(us)


def write_bins(channel: int, bins: list[int], fw: int, presets: int, preset: int = 0) -> bytes:
    """[preset] ch+1 (0 = master) + 0x200 x u16 (fw >= 207) else 0x400 x u8."""
    out = _p(presets, preset) + u8(channel)
    if fw >= 0xCF:
        return out + b''.join(u16(v) for v in bins[:0x200])
    return out + bytes(v & 0xFF for v in bins[:0x400])


def write_biquads(channel: int, stages, presets: int, preset: int = 0) -> bytes:
    out = _p(presets, preset) + u8(channel + 1) + u8(len(stages))
    for st in stages:
        out += b''.join(f32(c) for c in st[:5])
    return out


def write_head_unit(index: int, value: int) -> bytes: return u8(index) + u8(value)


def write_upmix0(rec: dict, fw: int, iterating: bool, preset: int) -> bytes:
    out = u8(0) + (u8(preset) if iterating and fw >= 0xD1 else b'')
    out += u8(rec['b0']) + u8(rec['b1']) + u8(rec.get('oneshot', 0)) + f32(rec['f4'])
    out += b''.join(f32(x) for x in rec['f8'][:4]) + f32(rec['f18']) + f32(rec['f1c'])
    return out + bytes(rec['b20'][:2]) + bytes(rec['b22'][:2])


def write_table(slot: int, table: list[int]) -> bytes:
    return u8(1) + u8(slot) + b''.join(u16(v) for v in table[:0x400])


def write_upmix(b0: int, b1: int, fa: float, fb: float, fc: float, fw: int, iterating: bool, preset: int) -> bytes:
    return u8(2) + (u8(preset) if iterating and fw >= 0xD1 else b'') + u8(b0) + u8(b1) + f32(fa) + f32(fb) + f32(fc)


def write_timealign(units: int, closest_x100: int, furthest_x100: int, fw: int, iterating: bool, preset: int) -> bytes:
    return u8(3) + (u8(preset) if iterating and fw >= 0xD1 else b'') + u8(units) + u16(closest_x100) + u16(furthest_x100)


# --- reply parsers (DSP) --------------------------------------------------
def parse_fw_version(d: bytes) -> dict:
    r = _Reader(d)
    if r.u8() != 0:
        raise ValueError('sub != 0')
    ver = r.u32()
    state = None
    if ver > 0xD2:
        if ver < 300:
            state = 0x17          # exe refuses 211..299 (FUN_14017c130); the reference 210 is fine
        else:
            ver = 0
    return {'fw': ver, 'state': state}


def parse_name(d: bytes) -> dict:
    r = _Reader(d)
    if r.u8() != 1:
        raise ValueError('sub != 1')
    # firmware replies [01][name][00], LEN = strlen + 2 (FW_MDI_HANDLERS_OCT22)
    return {'name': bytes(d[1:]).split(b'\0', 1)[0]}


def parse_preset_count(d: bytes) -> dict:
    r = _Reader(d); assert r.u8() == 2; return {'count': r.u8()}


def parse_current_preset(d: bytes) -> dict:
    r = _Reader(d); assert r.u8() == 3; return {'preset': r.u8()}


def parse_sourcesel(d: bytes) -> dict: return {'value': _Reader(d).u8()}


def parse_routing(d: bytes, fw: int, with_preset: bool, channel_count: int) -> dict:
    r = _Reader(d)
    if with_preset:
        r.u8()
    out = []
    while r.left() >= (8 if fw >= 0xCD else 1) and len(out) < channel_count:
        out.append(r.u64() if fw >= 0xCD else r.u8())
    return {'input_ids': out}


def parse_master(d: bytes, fw: int, with_preset: bool) -> dict:
    r = _Reader(d); out = {}
    if with_preset:
        out['preset'] = r.u8()
    out['channel'] = r.u8()
    if out['channel'] == 0:
        out['gain_raw'] = r.u16() if fw >= 0xCA else r.u8() << 8
        if fw < 200:
            r.u8(); r.u8(); r.u8(); r.u16(); r.u16()
    return out


def parse_channel(d: bytes, fw: int, with_preset: bool) -> dict:
    """0x0710 reply order (§3.7): gain, invert, passthrough, mute, u16@0x470 (test tone), delay, limiter."""
    r = _Reader(d); out = {}
    if with_preset:
        out['preset'] = r.u8()
    out['channel'] = r.u8() - 1
    out['gain_raw'] = r.u16() if fw >= 0xCA else r.u8() << 8
    out['invert'] = r.u8()
    pt = r.u8(); out['passthru'] = (1 if pt else 0) if fw < 0xD1 else pt
    out['mute'] = r.u8()
    out['testtone'] = r.u16()
    out['delay_us'] = r.u16()
    if fw > 0xCD:
        out['limiter_raw'] = r.u32()
    out['testtone_type'] = 0        # rec+0x472 := 0 on read
    return out


def parse_chan_props(d: bytes, fw: int, with_preset: bool) -> dict:
    r = _Reader(d); assert r.u8() == 2; out = {}
    if with_preset:
        out['preset'] = r.u8()
    out['channel'] = r.u8() - 1
    out['ordinal'] = r.u8()
    if fw < 200:
        r.u8()
    out['xoverlink'] = r.u8()
    return out


def parse_filter(d: bytes, fw: int, with_preset: bool) -> dict:
    r = _Reader(d); assert r.u8() == 3; out = {}
    if with_preset:
        out['preset'] = r.u8()
    out['channel'] = r.u8() - 1
    out['type'] = r.u8(); out['design'] = r.u8()
    if fw < 200:
        out.update(f1=int(r.f64()), f2=int(r.f64()), s1=int(r.f64()), s2=int(r.f64()))
    else:
        out.update(f1=r.u16(), f2=r.u16(), s1=r.u8(), s2=r.u8())
    return out


def _parse_points(r: _Reader, fw: int) -> list:
    pts = []
    if fw < 200:
        while r.left() > 0x20:
            r.u8(); pts.append((r.f64(), r.f64(), r.f64(), r.f64()))
    else:
        while r.left() > 9:
            pts.append((r.f32(), r.half(), r.half(), r.half()))
    return pts


def parse_chan_points(d: bytes, fw: int, with_preset: bool) -> dict:
    r = _Reader(d); assert r.u8() == 5; out = {}
    if with_preset:
        out['preset'] = r.u8()
    out['channel'] = r.u8() - 1
    out['points'] = _parse_points(r, fw)
    return out


def parse_master_points(d: bytes, fw: int, with_preset: bool) -> dict:
    r = _Reader(d); assert r.u8() == 6; out = {}
    if with_preset:
        out['preset'] = r.u8()
    if fw < 200:
        r.f64()
    out['points'] = _parse_points(r, fw)
    return out


def parse_input_gains(d: bytes, fw: int, with_preset: bool) -> dict:
    r = _Reader(d); assert r.u8() == 7; out = {}
    if with_preset:
        out['preset'] = r.u8()
    k0, kmax = (1, 0x40) if fw > 0xCB else (0, 7)
    vals = {}
    k = k0
    while k <= kmax and r.left() >= (2 if fw >= 0xCA else 1):
        vals[k] = r.u16() if fw >= 0xCA else r.u8() << 8
        k += 1
    out['raws'] = vals
    return out


def parse_input_delay(d: bytes, with_preset: bool) -> dict:
    r = _Reader(d); assert r.u8() == 8; out = {}
    if with_preset:
        out['preset'] = r.u8()
    out['us'] = r.u16()
    return out


def parse_head_unit(d: bytes) -> dict:
    r = _Reader(d); return {'index': r.u8(), 'value': r.u8()}


def parse_upmix0(d: bytes) -> dict:
    r = _Reader(d); assert r.u8() == 0
    return {'b0': r.u8(), 'b1': r.u8(), 'f4': r.f32(), 'f8': [r.f32() for _ in range(4)], 'f18': r.f32(),
            'f1c': r.f32(), 'b20': [r.u8(), r.u8()], 'b22': [r.u8(), r.u8()]}


def parse_table(d: bytes) -> dict:
    r = _Reader(d); assert r.u8() == 1; slot = min(r.u8(), 1)
    return {'slot': slot, 'table': [r.u16() for _ in range(0x400)]}


def parse_upmix(d: bytes) -> dict:
    r = _Reader(d); assert r.u8() == 2
    return {'b0': r.u8(), 'b1': r.u8(), 'fa': r.f32(), 'fb': r.f32(), 'fc': r.f32()}


def parse_timealign(d: bytes) -> dict:
    r = _Reader(d); assert r.u8() == 3
    return {'units': r.u8(), 'closest_x100': r.u16(), 'furthest_x100': r.u16()}


def parse_block_0718(d: bytes) -> dict:
    if len(d) != 6:
        raise ValueError('0x0718 reply needs len 6')
    r = _Reader(d); r.u16(); return {'dw': r.u32()}


def parse_block_read(d: bytes, kind: int, ident: int, limit: int) -> dict:
    r = _Reader(d)
    if r.u8() != kind:
        raise ValueError('type mismatch')
    got = r.u8() if kind in (6, 7) else r.u64()
    if got != ident:
        raise ValueError('id mismatch')
    return {'data': r.raw(min(r.left(), limit))}


# --------------------------------------------------------------------------
# coverage accounting
# --------------------------------------------------------------------------
def coverage() -> dict:
    tx = [s for s in SPECS if s.direction == 'tx']
    rx = [s for s in SPECS if s.direction != 'tx']
    return {
        'tx_total': len(tx), 'tx_pinned': sum(1 for s in tx if not s.unresolved),
        'tx_captured': sum(1 for s in tx if s.captured),
        'rx_total': len(rx), 'rx_pinned': sum(1 for s in rx if not s.unresolved),
        'unresolved': [(hex(s.cmd), s.sub, s.name, s.unresolved) for s in SPECS if s.unresolved],
    }


# --- DspTest 0x0719 (exe DSP_FIELD_MEANINGS item 9) -------------------------
LEVEL_KINDS = (6, 7, 8)   # u8-mask level reads; 8 exists only on custom firmware LB-RC<n> (stock: code 8)


def dsp_test(kind: int, ident: int) -> bytes:
    """[u8 type][u8 id] for types 6/7 (7 = RTA, id = channel) and our type 8 (LB-RC<n>: outputs 9-16),
    else [u8 type][u64 id]."""
    return u8(kind) + (u8(ident) if kind in LEVEL_KINDS else u64(ident))


def parse_dsp_test(d: bytes, kind: int, ident: int) -> dict:
    """Types 6/7 on 2.11.x firmware (FW thread, handler 0x518D48): reply
    [type][mask] then one signed int8 dB per set bit, lowest bit first
    (outputs with no DSP object are skipped). Other types: raw block."""
    if len(d) < 2 or d[0] != kind:
        raise ValueError('0x0719 reply type mismatch')
    if kind in LEVEL_KINDS:
        if d[1] != ident:
            raise ValueError('0x0719 reply mask mismatch')
        if len(d) - 2 > bin(ident).count('1'):         # LEN = 2 + popcount(mask)
            raise ValueError('0x0719 reply longer than its mask')
        vals = [b - 256 if b > 127 else b for b in d[2:]]
        return {'db': vals}
    return {'raw': bytes(d[9:])}


def level_dbfs(level: int) -> float:
    """region_f0_110:8163: Q31 magnitude -> dBFS (0 or below -> -inf)."""
    import math
    if level == 0:
        return float('-inf')
    x = level / 2147483647.0 if level > 0 else level * 2.0 ** -31
    return 20.0 * math.log10(abs(x))
