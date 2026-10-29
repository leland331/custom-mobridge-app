"""Firmware update checks, confirm, backup name and write-back (FUN_1400ab740 / FUN_14009dd70)."""
import json
import struct
import hashlib
import zlib

from mobridge import fif, flashflow as F, uirules as R


def make(tmp_path, product=0x202c, ver=(2, 11, 9), off=0x60000, pre=None, name='f.fif'):
    body = b'\x00' * 64
    entry = hashlib.md5(body).digest() + struct.pack('>IIII', product, ver[0] << 16 | ver[1] << 8 | ver[2], off, len(body))
    hdr = b'\xd3\x8b\xa6\x2c' + struct.pack('>II', 1, 1) + b'\0' * 16 + hashlib.md5(entry).digest() + entry
    pj = json.dumps(pre if pre is not None else {'DSPNV_VERS': 210, 'MIN_PREV_VERS': '2.0.0'}).encode() + b'\n\nGenerated'
    p = tmp_path / name
    p.write_bytes(pj.ljust(0x60, b'\0') + hdr + zlib.compress(body))
    return str(p)


def test_codes(tmp_path):
    assert F.validate(make(tmp_path), 0x202c, (2, 11, 8)).code == F.OK
    assert F.validate(make(tmp_path, product=0), 0x202c, (2, 11, 8)).code == F.OK       # wildcard
    assert F.validate(make(tmp_path, product=0x2019), 0x202c, (2, 11, 8)).code == F.INCOMPATIBLE_DEVICE
    assert F.validate(make(tmp_path), 0x202c, (2, 11, 9)).code == F.OLDER
    assert F.validate(make(tmp_path), 0x202c, (2, 11, 9), R.STATE_RECOVERY).code == F.OK
    assert F.validate(make(tmp_path, pre={'DSPNV_VERS': 211}), 0x202c, (2, 11, 8)).code == F.INCOMPATIBLE_APP
    assert F.validate(make(tmp_path, pre={'DSPNV_VERS': 210, 'MIN_PREV_VERS': '2.11.8'}), 0x202c, (2, 11, 6)).code == F.MIN_PREV
    assert F.validate(make(tmp_path, pre={'NVM_FIXED': True}), 0x202c, (2, 11, 8)).code == F.NVM_FIXED
    (tmp_path / 'bad.fif').write_bytes(b'junk')
    assert F.validate(str(tmp_path / 'bad.fif'), 0x202c, (2, 11, 8)).code == F.INVALID


def test_bootloader_guard_only(tmp_path):
    assert F.validate(make(tmp_path, off=0), 0x202c, (2, 11, 8)).guard
    assert not F.validate(make(tmp_path), 0x202c, (2, 11, 8)).guard
    assert fif.flash_problems(fif.load(make(tmp_path, product=0))[0]) == []


def test_stock_preamble_parses():
    import os
    p = os.environ.get('MOBRIDGE_STOCK_FIF', '')   # a stock 2.11.8 DA-G2.Pro A2B Ford image, if available
    if p and os.path.exists(p):
        assert fif.load(p)[0].preamble_json == {'DSPNV_VERS': 210, 'MIN_PREV_VERS': '2.0.0'}


def test_confirm_backup_writeback(tmp_path):
    plan = F.validate(make(tmp_path), 0x202c, (2, 11, 8))
    t = F.confirm_text(plan, (2, 11, 8))
    assert 'from version <b>2.11.8</b> to <b>2.11.9</b>' in t and 'A backup will automatically be made' in t
    import time
    stamp = time.strftime('%d-%m-%y_%H-%M-%S', time.localtime(0))
    assert F.backup_path('/b', 0x202c, 'DA-G2 Pro', 0).endswith('backup_202c_da-g2_pro_%s.rax' % stamp)
    assert not F.write_back_needed(plan, 210) and F.write_back_needed(plan, 209)
    found = F.find_min_prev([str(tmp_path)], (2, 11, 9))
    assert found and found.endswith('.fif')


class _FakeSession:
    def __init__(self, replies):
        from mobridge.framing import MdiFramer
        self.sent, self.replies, self.framer, self.t = [], list(replies), MdiFramer(), None

    def _send(self, cmd, payload):
        self.sent.append((cmd, payload))

    def _pump(self, timeout, want=None):
        return self.replies.pop(0) if self.replies else None


def _p(sub, arg=0):
    from mobridge.message import MdiMessage
    return MdiMessage(cmd=0x0200, data=sub.to_bytes(2, 'big') + arg.to_bytes(4, 'big', signed=True))


def test_flashfile_job(monkeypatch):
    from mobridge import zmodem
    from mobridge.message import MdiMessage
    got = []
    monkeypatch.setattr(zmodem, 'send_file', lambda t, data, name, **kw: got.append((data, name, kw['timeout'])))
    s = _FakeSession([MdiMessage(cmd=0x0201, data=b'\x01'), _p(0), _p(6, 100), _p(7, 50), _p(5), _p(8)])
    st = []
    assert F.run_flash(s, b'abc', 'x.fif', lambda *a: st.append(a))
    assert s.sent[0] == (0x0201, b'') and all(c == 0x0000 for c, _ in s.sent[1:])
    assert got == [(b'abc', 'x.fif', 300.0)] and ('position', 50, 100) in st and st[-1] == ('phase', 10)
    s = _FakeSession([MdiMessage(cmd=0x0201, data=b'\x01'), _p(9, -5)])
    assert not F.run_flash(s, b'abc', 'x.fif')
    s = _FakeSession([])
    assert not F.run_flash(s, b'abc', 'x.fif') and [c for c, _ in s.sent] == [0x0201] * 3
