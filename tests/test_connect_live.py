"""Live connect (connect.run_connect) driven against a fake device that answers
every read of the official DspRead sweep (cmdspec builders/parsers). Proves the
routine runs the proven sequence, reads the WHOLE tune into a DspModel, and
sends nothing but handshake/reads (no write command, no flash family)."""
import struct
import time

from mobridge import connect, parity, cmdspec as C, model as M
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage
from mobridge.transport import Transport

ED00 = 0xED00


class FakeMobridge(Transport):
    """A DA-G2.Pro-shaped device: fw 210, 4 presets, CH1 Rear Left Woofer (-6 dB,
    muted), CH4 Right Sub Woofer (-3 dB, delay 2.5 ms, limiter -20 dBFS, 2 PEQ
    points), CH12 Front Right Woofer; routing from DEVICE_CONFIG; Beeps -2 dB;
    input delay 10 ms; time align 0/150 cm; head unit bass 11."""
    FW = 210
    MAPPED = {0: (9, 0xE100, 0, 0, 1, 0, 0x7FFFFFFF),       # ch -> (ordinal, gain, inv, pt, mute, delay_us, limiter)
              3: (14, 0xE700, 0, 0, 0, 2500, 0x0CCCCCD0),
              11: (6, ED00, 1, 0, 0, 0, 0x7FFFFFFF)}
    ROUTING = {0: 0x01, 3: 0x03, 11: 0x3E}

    def __init__(self):
        self.fr = MdiFramer()
        self.out = bytearray()
        self.sent_frames = []
        self.baud = connect.POWERON_BAUD

    def _reply(self, cmd, d):
        if cmd == 0x0002: return b'\x01'
        if cmd == 0x0001: return bytes.fromhex('202c00000000202c00020b09')
        if cmd in (0x0020, 0x0025, 0x0282, 0x0400): return b'\x01'
        if cmd == 0x0714:
            sub = d[0]
            if sub == 0: return b'\x00' + struct.pack('>I', self.FW)
            if sub == 1: return b'\x01MyCar\x00'                     # [01][name][00], LEN = strlen+2
            if sub == 6: return d + b''                              # no master points
            if sub == 7: return d + b''.join(struct.pack('>H', 0xE900 if k == 3 else ED00) for k in range(1, 7))   # the unit returns 6 per preset
            if sub == 8: return d + struct.pack('>H', 10000)
            p, ch = d[1], d[2] - 1
            if ch not in self.MAPPED: return None
            if sub == 2: return bytes([2, p, ch + 1, self.MAPPED[ch][0], 0])
            if sub == 3: return bytes([3, p, ch + 1, 1 if ch == 3 else 0, 4 if ch == 3 else 0]) + struct.pack('>HHBB', 80, 2000, 24, 12)
            if sub == 5:
                pts = b''
                if ch == 3:
                    pts = C.write_chan_points(ch, [(100.0, -3.0, 2.0, 2.0), (2500.0, 1.5, 4.0, 3.0)], self.FW, 4, p)[3:]
                return bytes([5, p, ch + 1]) + pts
        if cmd == 0x0716:
            return b'\x02\x04' if d[0] == 2 else b'\x03\x00'
        if cmd == 0x0711:
            return b'\x00' + b''.join(struct.pack('>Q', self.ROUTING.get(ch, 0)) for ch in range(12))
        if cmd == 0x0713: return b'\x00'
        if cmd == 0x0710:
            p, ch = d[0], d[1]
            if ch == 0: return bytes([p, 0]) + struct.pack('>H', 0xED00 - 512)      # master -1 dB
            if ch - 1 not in self.MAPPED: return None
            o, g, inv, pt, mute, dus, lim = self.MAPPED[ch - 1]
            return bytes([p, ch]) + struct.pack('>HBBBHHI', g, inv, pt, mute, 0, dus, lim)
        if cmd == 0x070F:
            if d[0] == 2: return b'\x02\x00\x00' + struct.pack('>fff', 0.08, 0.0, 0.0)
            if d[0] == 3: return b'\x03\x00' + struct.pack('>HH', 0, 15000)
        if cmd == 0x0706:
            return bytes([d[0], 11 if d[0] == 0 else 7])
        if cmd == 0x0718 and d == b'\x00\x0c':
            return b'\x00\x0c' + struct.pack('>I', 0x4)               # Rear Fill on
        return None

    def write(self, data):
        self.raw = getattr(self, 'raw', bytearray()) + data
        for f in self.fr.feed(data):
            if f.kind == 'mdi' and f.message:
                self.sent_frames.append(f.message)
                body = self._reply(f.message.cmd, f.message.data)
                if body is None:
                    continue
                self.out += b'[WSNDMGR] noise\r\n'
                self.out += MdiFramer().encode(MdiMessage(cmd=f.message.cmd, data=body))

    def read(self, size=4096, timeout=0.1):
        d = bytes(self.out[:size]); del self.out[:size]
        if not d and timeout:
            time.sleep(min(timeout, 0.002))    # a port with nothing to read blocks; never spin
        return d

    def set_baud(self, baud):
        self.baud = baud


def test_run_connect_reads_the_whole_tune_into_the_model():
    dev = FakeMobridge()
    res = connect.run_connect(dev, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1)
    assert res.product_id == 0x202c and res.app_version == '2.11.9'
    assert dev.baud == connect.CONNECT_BAUD
    r = res.model
    assert r.fw == 210 and r.name == 'MyCar' and r.preset_count == 4 and r.current_preset == 0
    assert r.channels() == [0, 3, 11]
    assert r.channel_role[0] == 0x100 and r.channel_role[3] == 0x2000 and r.channel_role[11] == 0x20
    s = r.speaker(0x2000)
    assert abs(s.gain_db + 3.0) < 1e-6 and s.delay_ms == 2.5 and abs(s.limit_dbfs + 20.0) < 0.01
    assert s.filter_type == M.FT_LP and s.filter_design == 4 and (s.f1, s.f2, s.slope1, s.slope2) == (80, 2000, 24, 12)
    assert [(p.freq, p.gain) for p in s.eq.points] == [(100.0, -3.0), (2500.0, 1.5)]
    assert r.speaker(0x100).mute and r.speaker(0x20).invert_phase
    assert r.input_map[11] == 0x3E and r.input_map[3] == 0x03
    assert r.input_gains[0][3] == -2.0 and r.input_gains[0][1] == 0.0
    assert r.input_delay_ms[0] == 10.0
    assert abs(r.preset(0).master.gain_db + 1.0) < 1e-6
    assert r.preset(0).timealign.furthest == 150.0
    assert 0x0706 not in {m.cmd for m in dev.sent_frames}   # head unit: no read at connect (no dirty bits; 0x202c lacks flag bit 10)
    assert r.option(5) and not r.option(4) and not r.option(3)     # DspParamRead(0xc), fw 210 > 209
    assert all(sp.eq.mode == M.MODE_BIQUAD for sp in r.speakers_in_preset(0))   # fw >= 203: mode 2 everywhere
    assert r.eq_point_cap == 12                     # 0x180cf has no flag bit 17
    assert not r.is_modified()                      # clean after a read
    assert res.channel_gain[4] == s.gain_db          # legacy flat values still filled
    assert res.local_id is None                      # A2B product: no LocalID step


def test_connect_sends_only_handshake_and_reads():
    dev = FakeMobridge()
    connect.run_connect(dev, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1)
    f = MdiFramer()
    for msg in dev.sent_frames:
        assert parity.frame_cleared(f.encode(msg), msg.cmd)
    cmds = {m.cmd for m in dev.sent_frames}
    assert not cmds & {0x0700, 0x0701, 0x0702, 0x0703, 0x0704, 0x0705, 0x0707, 0x0708, 0x0100, 0x0101}
    assert not cmds & set(parity.LOCKED_COMMANDS)
    # the sweep follows the capture: AppName, AppVersion, preset count, current, routing, sourcesel ...
    order = [(m.cmd, m.data[:1]) for m in dev.sent_frames if m.cmd in (0x0714, 0x0716, 0x0711, 0x0713)][:6]
    assert order == [(0x0714, b'\x01'), (0x0714, b'\x00'), (0x0716, b'\x02'), (0x0716, b'\x03'),
                     (0x0711, b'\x00'), (0x0713, b'')]
    # ... then each preset-level step over all presets (step-major, preset-minor)
    reads = [(m.cmd, bytes(m.data)) for m in dev.sent_frames if m.cmd in (0x0710, 0x0714, 0x070F)]
    master = [d for c, d in reads if c == 0x0710 and d[1:2] == b'\x00']
    assert master == [bytes([p, 0]) for p in range(4)]
    i_m6 = [i for i, (c, d) in enumerate(reads) if c == 0x0714 and d[0] == 6]
    i_m7 = [i for i, (c, d) in enumerate(reads) if c == 0x0714 and d[0] == 7]
    assert [reads[i][1][1] for i in i_m6] == [0, 1, 2, 3] and max(i_m6) < min(i_m7)
    # channel 1: 0x0710 over presets, then sub 2, 3, 5 over presets
    ch1 = [(c, d[0] if c == 0x0714 else None, d[1] if c == 0x0714 else d[0]) for c, d in reads
           if (c == 0x0710 and d[1] == 1) or (c == 0x0714 and d[0] in (2, 3, 5) and d[2] == 1)]
    assert ch1 == ([(0x0710, None, p) for p in range(4)] + [(0x0714, 2, p) for p in range(4)]
                   + [(0x0714, 3, p) for p in range(4)] + [(0x0714, 5, p) for p in range(4)])
    # features last; 0x202c has no flag bit 8, so no 0x070f sub 0/1
    feats = [d[0] for c, d in reads if c == 0x070F]
    assert feats == [2, 2, 2, 2, 3, 3, 3, 3]
    assert [c for c, _d in reads][-8:] == [0x070F] * 8


def test_local_id_is_carried_but_never_enqueued_like_the_exe():
    # The exe has the LocalIDRead/Write jobs but nothing enqueues them: the connect
    # never sends 0x0100/0x0101, whatever mask is passed, on any product.
    for caps in (None, 0, connect.LOCALID_CAP):
        dev = FakeMobridge()
        res = connect.run_connect(dev, read_timeout=0.05, settle=0.0, capabilities=caps)
        assert not [m for m in dev.sent_frames if m.cmd in (0x0100, 0x0101)]
        assert res.local_id is None
    assert connect.session_capabilities(0x202c) == 0 and connect.session_capabilities(0x2002) == 0

    class Client:
        framer = MdiFramer()
        def __init__(self): self.sent = []
        def send_frame(self, fr): self.sent.append(fr)
    c = Client()
    assert not connect.write_local_id(c, 5, capabilities=0) and c.sent == []
    assert connect.write_local_id(c, 5, capabilities=connect.LOCALID_CAP)
    assert [int.from_bytes(x[3:5], 'big') for x in c.sent] == [0x0100, 0x0101]
    assert c.sent[1][5:] [:-1] == bytes.fromhex('0000000200000005')


def test_engine_applies_connect_result_and_goes_live():
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    import pytest
    pytest.importorskip('PySide6.QtWidgets')
    from PySide6 import QtWidgets
    from mobridge.gui.engine import Engine
    from mobridge.modes import DeviceMode
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dev = FakeMobridge()
    res = connect.run_connect(dev, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1)
    e = Engine()
    assert not e.connected
    e.root.speaker(0x2000).set_gain(-9.0)            # an offline edit: discarded on connect
    infos, models = [], []
    e.device_info.connect(infos.append)
    e.model_changed.connect(models.append)
    e._apply_connect_result(res, dev)
    assert e.connected and e.mode is DeviceMode.MDI
    assert e.metering
    assert infos and infos[-1]['product'] == 0x202c
    assert e.root is res.model and models == [res.model]     # the device's tune replaced the draft
    assert abs(e.root.speaker(0x2000).gain_db + 3.0) < 1e-6


def test_connect_receives_the_vehicle_file_before_reading():
    """After the 0x0282 [01] ack the device ZMODEM-sends temp.ini and routes every
    byte to ZMODEM: the connect must start a ZMODEM receive (ZRINIT) before the
    first read frame, and read the 0x0020 serial."""
    from mobridge import zmodem
    dev = FakeMobridge()
    dev._reply_orig = dev._reply
    dev._reply = lambda c, d: bytes(range(1, 9)) if c == 0x0020 else dev._reply_orig(c, d)
    res = connect.run_connect(dev, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1)
    raw = bytes(dev.raw)
    zrinit = zmodem.hex_header(zmodem.ZRINIT, bytes((0, 0x28, 0, 0x23)))
    first_read = MdiFramer().encode(MdiMessage(cmd=0x0714, data=b'\x01'))
    assert raw.index(zrinit) < raw.index(first_read)
    assert res.serial == '0102030405060708'


def test_connect_prefix_is_the_captured_order():
    """F3: the frames before the read sweep == capture frames 0-11 (stream_out.hex)."""
    dev = FakeMobridge()
    connect.run_connect(dev, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1)
    raw = bytes(dev.raw)
    assert raw.startswith(connect.CONNECT_ABORT_SEQ)
    want = b''.join(f for _s, f in connect.handshake_frames())
    assert raw[len(connect.CONNECT_ABORT_SEQ):].startswith(want)


def test_baud_stays_when_setbaud_refused():
    """F4: no switch without a 0x0002 reply status 01."""
    dev = FakeMobridge()
    orig = dev._reply
    dev._reply = lambda c, d: b'\x00' if c == 0x0002 else orig(c, d)
    connect.run_connect(dev, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1)
    assert getattr(dev, 'baud', None) != connect.CONNECT_BAUD


def test_input_gain_slots_decode_like_the_exe():
    """F11/F13: all 64 slots go through FUN_1400ece60; slots the device doesn't return
    come from the zeroed dev table -> -48 dB -> 0x8D00 on write."""
    from mobridge import devread, dspwrite
    devread.Reader.dev_input_raw.clear()
    dev = FakeMobridge()
    res = connect.run_connect(dev, read_timeout=0.05, settle=0.0, zmodem_timeout=0.1)
    g = res.model.input_gains[0]
    assert all(-48.0 <= g[k] <= 9.0 for k in range(1, 7))   # the unit returns 6 per preset
    assert g[64] == -48.0
    pl = dspwrite.input_gains_payload(res.model, 0)
    assert pl[2 + 63 * 2: 2 + 64 * 2] == b'\x8d\x00'
