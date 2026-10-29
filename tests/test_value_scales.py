"""Value-scale decoders cracked from the native app decoder FUN_1400f8c50
(VALUE_MAPPING_AUDIT-20261021T034107). All are DISPLAY/decode only: candidate=True
so decode works, confirmed=False so encode() (sending) still refuses. Numbers are
anchored to the audit's own code+device data points."""
import struct

import pytest

from mobridge import encoders as e


def _d(name, raw):
    return e.ENCODERS[name].decode(raw)


def test_gain_and_eq_magnitude_scale():
    e.set_gain_unity_for_fw(208)  # the reference unit (>v207) -> unity 0xED00
    assert round(_d('gain_db', struct.pack('<h', -4864)), 2) == 0.0    # 0xED00
    assert round(_d('gain_db', struct.pack('<h', -4915)), 2) == -0.1   # 0xECCD
    assert round(_d('gain_db', struct.pack('<h', -6400)), 2) == -3.0   # 0xE700 (sub)
    # EQ band gain is NOT the gain scale: IEEE half-float dB (0x4200 = +3.0, captured 10-22)
    assert _d('eq_gain', bytes.fromhex('4200')) == 3.0
    assert _d('eq_gain', bytes.fromhex('4500')) == 5.0
    assert _d('eq_q', bytes.fromhex('4500')) == 5.0      # Q is half-float too


def test_gain_firmware_version_gate():
    # fw <= v207 shifts the zero point to 0xE700; fw > v207 uses 0xED00.
    e.set_gain_unity_for_fw(207)
    assert round(_d('gain_db', struct.pack('<h', -6400)), 2) == 0.0
    e.set_gain_unity_for_fw(208)
    assert round(_d('gain_db', struct.pack('<h', -4864)), 2) == 0.0


def test_freq_slope_delay_limiter_scales():
    assert _d('eq_freq', b'\x00\x96') == 150.0     # captured crossover 0x0096
    assert _d('xover_freq', b'\x07\xd0') == 2000.0  # captured 0x07d0
    assert _d('xover_slope', b'\x00\x0c') == 12.0   # captured 0x0c = 12 dB/oct
    assert _d('delay_time', struct.pack('>H', 10000)) == 10.0   # 10000us -> 10ms
    assert round(_d('limiter_thresh', bytes.fromhex('7fffffff')), 3) == 0.0  # OFF
    assert round(_d('limiter_thresh', struct.pack('>i', 2 ** 30)), 2) == -6.02


def test_flags_are_bools():
    for fl in ('phase', 'passthru', 'mute'):
        assert _d(fl, b'\x01') == 1.0 and _d(fl, b'\x00') == 0.0


def test_all_cracked_scales_still_refuse_to_send():
    # Verified SCALE does not open the SEND gate: encode() must raise for every one.
    for name in ('gain_db', 'eq_gain', 'eq_freq', 'xover_freq', 'xover_slope',
                 'delay_time', 'limiter_thresh', 'phase', 'passthru', 'mute'):
        with pytest.raises(e.EncodingUnconfirmed):
            e.ENCODERS[name].encode(0.0)
        assert e.ENCODERS[name].confirmed is False


def test_still_soft_params_refuse_even_to_decode():
    # Ripple is not a wire scalar at all (folded into the 0x0708 coefficients), so it
    # stays fully unconfirmed. (eq_q was resolved by the 10-22 capture: half-float.)
    for name in ('ripple',):
        with pytest.raises(e.EncodingUnconfirmed):
            e.ENCODERS[name].decode(b'\x00\x00')
    with pytest.raises(e.EncodingUnconfirmed):
        e.ENCODERS['eq_q'].encode(5.0)       # decodes, but still never sends


# --- DSPNV version gate wired to the engine + the SEPARATE .rax legacy offset ----
def test_engine_applies_dspnv_version_to_gain_unity(app):
    import struct
    from mobridge.gui.engine import Engine
    eng = Engine()
    eng.apply_dspnv_version(210)   # the reference unit -> 0xED00
    assert round(e.ENCODERS['gain_db'].decode(struct.pack('<h', -4864)), 2) == 0.0
    assert eng.dspnv_version == 210
    eng.apply_dspnv_version(200)   # pre-v208 unit -> 0xE700 zero point
    assert round(e.ENCODERS['gain_db'].decode(struct.pack('<h', -6400)), 2) == 0.0


def test_rax_legacy_offset_is_file_import_only():
    from mobridge import stored_config as sc
    # fires ONLY when importing a pre-v208 file into a >v207 model
    assert sc.rax_legacy_gain_offset(210, 207) == -3.0
    # not for a current-version file, not for an old model, not otherwise
    assert sc.rax_legacy_gain_offset(210, 208) == 0.0
    assert sc.rax_legacy_gain_offset(207, 100) == 0.0
    assert sc.rax_legacy_gain_offset(210, 210) == 0.0


# --- DSPNV version read from the NVM dump (CRC-verified) -------------------------
import os as _os
_NVM_NAME = 'nvm_oct20_settings_0x60000.bin'
_DUMP_DIR = '/mnt/project-files/device-dumps'
_DUMP_ZIP = '/mnt/project-files/device-dumps.zip'


def _find_dump(tmp_path):
    """Path to the reference NVM settings dump, extracting it from the shared
    device-dumps.zip into tmp if not already unpacked. None if unavailable."""
    p = _os.path.join(_DUMP_DIR, _NVM_NAME)
    if _os.path.exists(p):
        return p
    if _os.path.exists(_DUMP_ZIP):
        import zipfile
        with zipfile.ZipFile(_DUMP_ZIP) as z:
            for n in z.namelist():
                if n.endswith(_NVM_NAME):
                    out = _os.path.join(str(tmp_path), _NVM_NAME)
                    with z.open(n) as src, open(out, 'wb') as dst:
                        dst.write(src.read())
                    return out
    return None


def test_dspnv_version_from_real_dump_crc_ok(tmp_path):
    from mobridge import stored_config as sc
    p = _find_dump(tmp_path)
    if not p:
        import pytest; pytest.skip('reference NVM dump not available here')
    info = sc.dspnv_version(open(p, 'rb').read())
    assert info['version'] == 210          # the reference unit
    assert info['crc_ok'] is True
    assert info['stored_crc'] == 0x668caf10


def test_load_nvm_version_applies_only_when_crc_ok(app):
    import struct, zlib
    from mobridge import stored_config as sc, encoders
    from mobridge.gui.engine import Engine
    # Build a minimal settings-partition buffer: version 200 @0xA10 + a correct CRC.
    buf = bytearray(sc.DSPNV_VERSION_OFF + sc.DSPNV_CRC_LEN)
    struct.pack_into('<I', buf, sc.DSPNV_VERSION_OFF, 200)
    crc = zlib.crc32(bytes(buf[sc.DSPNV_VERSION_OFF:sc.DSPNV_VERSION_OFF + sc.DSPNV_CRC_LEN])) & 0xFFFFFFFF
    struct.pack_into('<I', buf, sc.DSPNV_CRC_OFF, crc)
    eng = Engine()
    info = eng.load_nvm_version(bytes(buf))
    assert info['crc_ok'] and info['version'] == 200 and eng.dspnv_version == 200
    assert encoders._gain_unity == encoders._GAIN_UNITY_OLD   # 200 <= 207 -> 0xE700
    # Corrupt the CRC: version must be IGNORED, unity unchanged (stays old here).
    struct.pack_into('<I', buf, sc.DSPNV_CRC_OFF, crc ^ 0xFFFF)
    eng2 = Engine()
    info2 = eng2.load_nvm_version(bytes(buf))
    assert info2['crc_ok'] is False
    assert getattr(eng2, 'dspnv_version', None) is None       # not applied
