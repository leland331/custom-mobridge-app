"""chimelog against a fake device stream: connect is the read-only handshake,
then only keep-alives go out; 0x0401 lines and 0x0615/0x0646 events land in
.txt and .jsonl, chime lines are highlighted, and data is on disk without close()."""
import os
import pytest
import json
import struct

from mobridge import chimelog, connect
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage

from test_connect_live import FakeMobridge


def _mdi(cmd, data):
    return MdiFramer().encode(MdiMessage(cmd=cmd, data=data))


def _can(cid, data):
    return struct.pack('>IHBI', cid, 0, len(data), 0) + data


class StreamingMobridge(FakeMobridge):
    """After connect, streams a scripted log/bus sequence (split mid-line)."""

    def __init__(self, stream):
        super().__init__()
        self.stream = list(stream)
        self.connected = False

    def read(self, size=4096, timeout=0.1):
        if self.connected and not self.out and self.stream:
            self.out += self.stream.pop(0)
        return super().read(size, timeout)


def _run(tmp_path, stream, echo):
    dev = StreamingMobridge(stream)
    log = chimelog.ChimeLog(str(tmp_path / 'run'), echo=echo.append, sync_s=0.0)
    real = connect.run_connect

    def wrapped(t, **kw):
        res = real(t, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1, **kw)
        dev.n_connect = len(dev.sent_frames)
        dev.connected = True
        return res
    connect.run_connect, saved = wrapped, real
    try:
        chimelog.record(dev, log, duration=1.3, poll_s=0.01)
    finally:
        connect.run_connect = saved
    return dev, log


def test_records_log_and_bus_events_read_only(tmp_path):
    stream = [
        _mdi(0x0401, b'handle_chimes: ID: 0x'),
        _mdi(0x0401, b'11\r\nWSNDMGR_CancelWarning: 255\r\n'),
        _mdi(0x0615, _can(0x2F4, bytes([0x40, 0x12, 0, 0, 0, 0, 0, 0x0C]))),
        _mdi(0x0615, _can(0x2A0, bytes(8))),
        _mdi(0x0646, bytes([3, 1, 2])),
        _mdi(0x0401, b'Audio turned on\r\n'),
    ]
    echo = []
    dev, log = _run(tmp_path, stream, echo)
    # nothing but the connect handshake and 0x0001 keep-alives after it
    after = dev.sent_frames[dev.n_connect:]
    assert after and all(m.cmd == 0x0001 and m.data == b'' for m in after)
    # on disk before close(): power loss loses nothing already recorded
    recs = [json.loads(l) for l in (tmp_path / 'run.jsonl').read_text().splitlines()]
    logs = [r for r in recs if r['kind'] == 'log']
    assert any(r['text'] == 'handle_chimes: ID: 0x11' and r['chime_id'] == 0x11 and r['chime'] for r in logs)
    assert any(r['text'].startswith('WSNDMGR_CancelWarning') and not r['chime'] for r in logs)
    can = [r for r in recs if r.get('bus') == 'can']
    assert can[0]['id'] == '0x2f4' and can[0]['chime_id'] == 0x12 and can[0]['chime']
    assert can[1]['id'] == '0x2a0' and not can[1]['chime']
    assert any(r.get('bus') == 'a2b' for r in recs)
    assert log.chime_ids == {0x11: 1, 0x12: 1}
    txt = (tmp_path / 'run.txt').read_text()
    assert '*** handle_chimes: ID: 0x11' in txt and 'Audio turned on' in txt
    assert any('handle_chimes' in e for e in echo) and not any('CancelWarning' in e for e in echo)
    log.close()
    assert 'chime IDs seen' in log.summary()


def test_no_keepalive_sends_nothing_after_connect(tmp_path):
    dev = StreamingMobridge([_mdi(0x0401, b'x\r\n')])
    log = chimelog.ChimeLog(str(tmp_path / 'q'), echo=lambda s: None)
    n = [0]
    real = connect.run_connect

    def wrapped(t, **kw):
        res = real(t, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1, **kw)
        n[0] = len(dev.sent_frames); dev.connected = True
        return res
    connect.run_connect = wrapped
    try:
        chimelog.record(dev, log, duration=1.2, keepalive=False, poll_s=0.01)
    finally:
        connect.run_connect = real
    assert len(dev.sent_frames) == n[0]
    log.close()


def _patched(dev):
    real = connect.run_connect

    def wrapped(t, **kw):
        res = real(t, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1, **kw)
        dev.n_connect = len(dev.sent_frames); dev.connected = True
        return res
    return real, wrapped


def test_selftest_pass_and_status_file(tmp_path):
    dev = StreamingMobridge([_mdi(0x0401, b'a\r\nb\r\nhandle_chimes: ID: 0x0b\r\n')])
    log = chimelog.ChimeLog(str(tmp_path / 's'), echo=lambda s: None)
    log.status_path = str(tmp_path / 'chimelog-status.json'); log.boot = 7
    out = []
    real, connect.run_connect = _patched(dev)
    try:
        assert chimelog.selftest(dev, log, seconds=3, out=out.append)
    finally:
        connect.run_connect = real
    assert out[0].startswith('PASS')
    log.close()
    st = json.loads((tmp_path / 'chimelog-status.json').read_text())
    assert st['last_chime']['id'] == '0x0b' and st['log_lines'] >= 3 and st['boot'] == 7
    rec = json.loads((tmp_path / 's.jsonl').read_text().splitlines()[-1])
    assert 'mono' in rec and rec['boot'] == 7


def test_selftest_fails_without_log_lines(tmp_path):
    class Quiet(StreamingMobridge):
        def write(self, data):
            super().write(data)
            self.out = bytearray(bytes(self.out).replace(b'[WSNDMGR] noise\r\n', b''))
    dev = Quiet([])
    log = chimelog.ChimeLog(str(tmp_path / 'f'), echo=lambda s: None)
    out = []
    real, connect.run_connect = _patched(dev)
    try:
        ok = chimelog.selftest(dev, log, seconds=1, out=out.append)
    finally:
        connect.run_connect = real
    log.close()
    assert not ok and out[0].startswith('FAIL')


@pytest.mark.skipif(not os.path.exists('/proc/sys/kernel/random/boot_id'),
                    reason='boot counting needs the Linux boot_id (the logger runs on the bridge)')
def test_boot_number_counts_new_boots(tmp_path):
    f = str(tmp_path / '.bootcount')
    n = chimelog.boot_number(f)
    assert chimelog.boot_number(f) == n          # same boot: unchanged
    open(f, 'w').write('41 some-old-boot\n')
    assert chimelog.boot_number(f) == 42


# --- --enable-chime-log -------------------------------------------------------
PROVEN = [bytes.fromhex(h) for h in (
    'aa000104016496', 'aa000104010ded',                                      # d, Enter
    'aa000104016199', 'aa000104016c8e', 'aa000104016c8e', 'aa000104010ded')]  # a l l, Enter


class RawRecorder(StreamingMobridge):
    def __init__(self, stream):
        super().__init__(stream)
        self.raw_after = []

    def write(self, data):
        if self.connected:
            self.raw_after.append(bytes(data))
        super().write(data)


def _record(dev, log, **kw):
    real, connect.run_connect = _patched(dev)
    try:
        chimelog.record(dev, log, poll_s=0.01, **kw)
    finally:
        connect.run_connect = real


def test_flag_off_sends_no_console_text(tmp_path):
    dev = RawRecorder([_mdi(0x0401, b'x\r\n')])
    log = chimelog.ChimeLog(str(tmp_path / 'off'), echo=lambda s: None, sync_s=0.0)
    _record(dev, log, duration=1.3)
    after = dev.sent_frames[dev.n_connect:]
    assert after and all(m.cmd == 0x0001 for m in after)
    assert not [w for w in dev.raw_after if w[3:5] == b'\x04\x01']
    log.close()


def test_flag_on_sends_exact_frames_once_per_connect_and_after_reconnect(tmp_path):
    log = chimelog.ChimeLog(str(tmp_path / 'on'), echo=lambda s: None, sync_s=0.0)
    for _ in range(2):                                   # second pass = reconnect
        dev = RawRecorder([_mdi(0x0401, b'handle_chimes: ID: 0x0b\r\n')])
        _record(dev, log, duration=1.0, enable_chime_log=True, enable_gap_s=0.2)
        console = [w for w in dev.raw_after if w[3:5] == b'\x04\x01']
        assert console == PROVEN                         # exact bytes, in order, once
        others = [m for m in dev.sent_frames[dev.n_connect:] if m.cmd != 0x0401]
        assert all(m.cmd == 0x0001 for m in others)
    assert log.chime_log_enabled and log.handle_chimes_lines == 2
    log.close()
    sent = [json.loads(l) for l in (tmp_path / 'on.jsonl').read_text().splitlines()]
    sent = [r for r in sent if r['kind'] == 'sent']
    assert [r['line'] for r in sent] == ['d', 'all', 'd', 'all']
    assert [bytes.fromhex(h) for r in sent[:2] for h in r['frames']] == PROVEN


def test_enable_frames_match_encoder_and_gate():
    assert chimelog.enable_frames('d') + chimelog.enable_frames('all') == PROVEN


def test_selftest_reports_handle_chimes_after_enable(tmp_path):
    dev = RawRecorder([_mdi(0x0401, b'a\r\nb\r\nhandle_chimes: ID: 0x0b\r\n')])
    log = chimelog.ChimeLog(str(tmp_path / 'st'), echo=lambda s: None)
    out = []
    real, connect.run_connect = _patched(dev)
    try:
        assert chimelog.selftest(dev, log, seconds=1, out=out.append, enable_chime_log=True)
    finally:
        connect.run_connect = real
    log.close()
    assert out[0].startswith('PASS') and out[1].startswith('chime log: ON - 1')
