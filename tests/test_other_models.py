"""Other Mobridge models (DEVICE_COMPAT_AUDIT.md): per-product behaviour from the
official product table, no Ford fallback for unknown products, and the WIZARD /
LW link modes against synthesised traffic. No device involved."""
import time

import pytest

from mobridge import connect, dspwrite, features, linkmodes as L, model as M, products as P, uirules as R
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage
from mobridge.modes import DeviceMode
from mobridge.transport import Transport

from test_connect_live import FakeMobridge

# one unit per family in the official table (PRODUCT_TABLE.md), plus Dante (0 channels)
FAMILIES = {
    0x202c: 'A2B Pro Ford', 0x2046: 'A2B Pro GM', 0x2040: 'A2B Pro Harley', 0x2031: 'A2B Pro Generic',
    0x202b: 'A2B Standard Ford', 0x2045: 'A2B Standard GM', 0x202a: 'A2B Entry Ford',
    0x2002: 'MOST25 Pro', 0x2019: 'MOST150 Pro', 0x2024: 'MOST25 Standard', 0x2025: 'MOST150 Standard',
    0x2029: 'MOST150 Entry', 0x201d: 'UNICENS150', 0x5000: 'DA-G3', 0x5002: 'DA-G3 A2B',
    0xd005: 'K2 MOST25', 0x2032: 'K2 A2B', 0x2001: 'K1', 0xf00e: 'master node', 0xf00d: 'master node 16',
    0x0011: 'legacy DA', 0x8000: 'legacy NAV-TV', 0x4002: 'Morgan', 0x4000: 'Dante',
}
UNKNOWN = 0x1234


def ident(pid, ver=(2, 11, 9)):
    return pid.to_bytes(2, 'big') + bytes(5) + pid.to_bytes(2, 'big')[:2] + bytes(ver)


class ModelDevice(FakeMobridge):
    def __init__(self, pid):
        super().__init__()
        self.pid = pid

    def _reply(self, cmd, d):
        if cmd == 0x0001:
            return ident(self.pid)
        return super()._reply(cmd, d)


@pytest.mark.parametrize('pid', sorted(FAMILIES))
def test_connect_reads_each_family_by_its_own_table_row(pid):
    dev = ModelDevice(pid)
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0, zmodem_timeout=0.05)
    assert res.mode is DeviceMode.MDI and res.product_id == pid and res.app_version == '2.11.9'
    cmds = {m.cmd for m in dev.sent_frames}
    n = P.channel_count(pid)
    if n == 0:                                            # Dante: nothing to read (FUN_140099030)
        assert not any(0x0700 <= c < 0x0800 for c in cmds)
        return
    assert res.model.channel_count == n and res.model.product == pid
    flags = P.flags(pid)
    if not flags >> 10 & 1:
        assert 0x0706 not in cmds                         # head unit read needs flag bit 10
    assert res.model.eq_point_cap == (15 if flags >> 17 & 1 else 12)   # master node 0xf00e: 15 points


def test_unknown_product_reads_nothing_and_enables_nothing():
    dev = ModelDevice(UNKNOWN)
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0, zmodem_timeout=0.05)
    assert res.product_id == UNKNOWN
    assert not any(0x0700 <= m.cmd < 0x0800 for m in dev.sent_frames)
    assert P.flags(UNKNOWN) == 0 and P.channel_count(UNKNOWN) == 0
    from mobridge.gui.official.screens import _product_flags
    assert _product_flags(M.DspModel(fw=210, product=UNKNOWN)) == 0       # no Ford fallback
    assert _product_flags(M.DspModel(fw=210, product=0x2046)) == P.flags(0x2046)


def _edited(pid):
    root = M.DspModel(fw=210, product=pid)
    root.channel_count = 12
    root.map_speaker(0x2000, 3)
    root.speaker(0x2000).set_gain(-3.0)
    root.preset(0).master.set_gain(1.0)
    root.set_option(5, True) if hasattr(root, 'set_option') else None
    return root


def test_unknown_product_write_filter_fails_closed():
    """dspwrite.py used 0xffffffff (everything passes) for a product not in the
    table; now it filters like a product with no flags (the exe finds no record)."""
    unknown = dspwrite.flush(_edited(UNKNOWN))
    closed = dspwrite.flush(_edited(UNKNOWN), product_flags=0)
    assert unknown.cmds() == closed.cmds() and unknown.mask == closed.mask
    assert unknown.param_write is None
    assert unknown.mask & ~P.request_filter(0xffffffff, 0) == 0


@pytest.mark.parametrize('pid', sorted(FAMILIES))
def test_tabs_and_editors_follow_the_product_flags(pid):
    flags, n = P.flags(pid), P.channel_count(pid)
    tv = R.tab_visibility(True, flags, 210, n, True)
    if n == 0:
        assert not tv['channel_eq'] and not tv['time_align']
        return
    assert tv['time_align'] == bool(flags & R.CAP_TIME_ALIGN)
    assert tv['channel_eq'] == bool(flags & (R.CAP_CHANNEL_EQ | R.CAP_CROSSOVER))
    assert tv['config_text'] == ('CONFIG && MASTER EQ' if flags & R.CAP_MASTER_EQ else 'CONFIGURATION')
    assert len(P.channel_labels(pid)) == n


@pytest.mark.parametrize('pid', sorted(FAMILIES))
def test_custom_features_need_the_lb_build_tag(pid):
    assert features.active(None) == frozenset()
    assert features.active('2.11.9') == frozenset()       # a stock build string on any model
    assert features.active('LB-RC1') == {'rear_chimes', 'meters12'}


# --- WIZARD ---------------------------------------------------------------------
def test_wizard_frames_round_trip_byte_exact():
    f = L.WizardFrame(4, 0x0106, b'\x01\x02')
    wire = L.wizard_encode(f)
    assert wire[:2] == b'\xff\x55' and wire[2] == 5 and wire[3] == 4 and wire[4:6] == b'\x01\x06'
    assert (sum(wire[2:]) & 0xff) == 0                    # -sum checksum, sync excluded
    assert L.WizardParser().feed(b'junk' + wire) == [f]
    g = L.WizardFrame(2, 0x07, bytes(range(256)) * 2)     # extended length 00 hi lo
    w2 = L.wizard_encode(g)
    assert w2[2] == 0 and int.from_bytes(w2[3:5], 'big') == 2 + 512
    p = L.WizardParser()
    assert p.feed(w2[:7]) == [] and p.feed(w2[7:]) == [g]   # split across reads
    bad = bytearray(wire); bad[-1] ^= 1
    p = L.WizardParser()
    assert p.feed(bytes(bad) + wire) == [f] and p.errors == 1
    with pytest.raises(ValueError):
        L.wizard_encode(L.WizardFrame(1, 0, bytes(0x401)))


# --- LW ---------------------------------------------------------------------------
def test_lw_tokens_byte_exact():
    assert L.lw_knock() == bytes.fromhex('75de233da1')
    assert L.lw_null() == bytes(66) and L.lw_identify_query() == b'i'
    toks = L.LwParser().feed(bytes.fromhex('55bb452e7c') + b'I\x00\x46\x20\x02\x0b\x09' + b'Dboot 1.2\x00' + b'X' + b'R\x01')
    assert [t.char for t in toks] == ['U', 'I', 'D', 'X', 'R']
    assert toks[0].word == L.LW_BOOT_MAGIC
    assert toks[1].ident == (0x2046, (2, 11, 9))
    assert toks[2].data == b'boot 1.2' and toks[4].data == b'\x01'


def test_detect_mode():
    assert L.detect_mode(b'') is DeviceMode.UNKNOWN
    assert L.detect_mode(b'\x00' + bytes.fromhex('55bb452e7c')) is DeviceMode.LW
    w = L.wizard_encode(L.WizardFrame(4, 0x0103))
    assert L.detect_mode(w + L.wizard_encode(L.WizardFrame(1, 2, b'ok'))) is DeviceMode.WIZARD
    assert L.detect_mode(w) is DeviceMode.UNKNOWN          # one frame is not evidence
    assert L.detect_mode(b'\r\nProd=0x2019, Ver=2.0.0\r\n') is DeviceMode.BOOTS
    assert L.detect_mode(b'OK\r\n') is DeviceMode.BOOTS
    assert L.detect_mode(b'[WSNDMGR] noise\r\n') is DeviceMode.UNKNOWN


class LwDevice(Transport):
    """A unit on the LW token console: ignores MDI, answers `u`+key with
    U+magic and `i` with I (product, version)."""

    def __init__(self, pid=0x2001, chatter=True):
        self.pid, self.raw, self.out = pid, bytearray(), bytearray()
        if chatter:
            self.out += bytes.fromhex('55bb452e7c')        # boot banner token

    def write(self, data):
        self.raw += data
        if data == L.lw_knock():
            self.out += bytes.fromhex('55bb452e7c')
        elif data == b'i':
            self.out += b'I\x00' + self.pid.to_bytes(2, 'little') + bytes([1, 4, 2])

    def read(self, size=4096, timeout=0.1):
        d = bytes(self.out[:size]); del self.out[:size]
        if not d and timeout:
            time.sleep(min(timeout, 0.002))    # a port with nothing to read blocks; never spin
        return d

    def flush_input(self):
        pass

    def set_baud(self, baud):
        pass


def test_connect_identifies_an_lw_unit_with_read_only_tokens():
    dev = LwDevice()
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0)
    assert res.mode is DeviceMode.LW and res.product_id == 0x2001 and res.app_version == '1.4.2'
    lw_part = bytes(dev.raw[dev.raw.index(bytes(66)):])
    assert lw_part == bytes(66) + L.lw_knock() + b'i'       # null, knock, identify; nothing else
    assert res.model is None


class TextDevice(LwDevice):
    def __init__(self, banner):
        super().__init__(chatter=False)
        self.out += banner

    def write(self, data):
        self.raw += data


def test_connect_reports_wizard_and_boots_without_sending_more():
    w = L.wizard_encode(L.WizardFrame(4, 0x0103)) + L.wizard_encode(L.WizardFrame(4, 0x0104))
    for banner, mode in ((w, DeviceMode.WIZARD), (b'Prod=0x2002, Ver=2.0.0\r\n', DeviceMode.BOOTS)):
        dev = TextDevice(banner)
        res = connect.run_connect(dev, read_timeout=0.02, settle=0.0)
        assert res.mode is mode and res.model is None
        frames = [f.message.cmd for f in MdiFramer().feed(bytes(dev.raw)) if f.kind == 'mdi']
        # the full old MDI open runs first (reads only), then nothing more: no DspRead
        assert frames == [0x0000, 0x0000, 0x0002, 0x0000, 0x0000, 0x0002, 0x0001,
                          0x0020, 0x0025, 0x0400, 0x0001, 0x0282]


def test_silent_unit_still_follows_the_old_path():
    dev = TextDevice(b'')
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0, zmodem_timeout=0.05)
    assert res.mode is DeviceMode.MDI and res.product_id is None


# --- a normal MDI unit that is slow must never be taken for another link mode ------
class SlowFord(FakeMobridge):
    """the reference DA-G2.Pro (0x202c), waking up: prints console-looking boot lines
    (READY / OK, bytes the BOOTS detector knows) and stays deaf to the first
    `deaf` MDI requests, or garbles the first reply (a lost first frame)."""

    def __init__(self, deaf=0, garble_first=False, boot=b'READY\r\nOK\r\n'):
        super().__init__()
        self.deaf, self.garble = deaf, garble_first
        self.out += boot

    def write(self, data):
        if self.deaf:
            self.deaf -= 1
            self.raw = getattr(self, 'raw', bytearray()) + data
            for f in self.fr.feed(data):
                if f.kind == 'mdi' and f.message:
                    self.sent_frames.append(f.message)
            return
        n = len(self.out)
        super().write(data)
        if self.garble and len(self.out) > n:
            del self.out[n:n + len(self.out[n:]) // 2 + 1]     # first reply loses its head
            self.garble = False


def _full_mdi_read(res, dev):
    assert res.mode is DeviceMode.MDI and res.product_id == 0x202c and res.app_version == '2.11.9'
    assert res.model is not None and res.model.channels() == [0, 3, 11]
    assert any(0x0700 <= m.cmd < 0x0800 for m in dev.sent_frames)    # the tune was read


@pytest.mark.parametrize('deaf', [1, 2, 6, 7])
def test_slow_ford_that_misses_the_identify_stays_mdi(deaf):
    """Deaf through the handshake and identify (and, at 7, the session open too):
    console-looking boot text must not make it BOOTS; the ping fills the id."""
    dev = SlowFord(deaf=deaf)
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0, zmodem_timeout=0.05)
    _full_mdi_read(res, dev)
    assert not any(c in bytes(dev.raw) for c in (L.lw_knock(),))     # never knocked LW


def test_ford_whose_first_frame_is_lost_stays_mdi():
    dev = SlowFord(garble_first=True)
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0, zmodem_timeout=0.05)
    _full_mdi_read(res, dev)


def test_ford_that_misses_the_identify_with_lw_like_noise_stays_mdi():
    """Even the LW boot token in the noise is no evidence once any MDI frame came back."""
    dev = SlowFord(deaf=7, boot=bytes.fromhex('55bb452e7c') + L.wizard_encode(L.WizardFrame(4, 1)))
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0, zmodem_timeout=0.05)
    _full_mdi_read(res, dev)
    assert L.lw_knock() not in bytes(dev.raw)


def test_boots_detection_needs_whole_lines():
    assert L.detect_mode(b'\x00\x01OKAY\x00') is DeviceMode.UNKNOWN
    assert L.detect_mode(b'\nREADYish') is DeviceMode.UNKNOWN


def test_wizard_detection_ignores_noise_around_the_baud_switch():
    """Wrong-rate garbage around the 0x0002 baud switch, salted with lone FF 55 frames
    whose checksum happens to match: never WIZARD. Two real frames in the same noise are."""
    import random
    rnd = random.Random(0x55)
    lucky = L.wizard_encode(L.WizardFrame(0, 0))            # a minimal frame noise can form
    one = two = 0
    for _ in range(2000):
        noise = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(16, 400)))
        cut = rnd.randint(0, len(noise))
        data = noise[:cut] + lucky + noise[cut:]
        if len(L.WizardParser().feed(data)) == 1:              # noise + one valid frame
            one += 1
            assert L.detect_mode(data) is not DeviceMode.WIZARD
    assert one > 1000                                          # the case was exercised
    for _ in range(200):
        noise = bytes(rnd.getrandbits(8) for _ in range(200))
        data = noise[:50] + lucky + noise[50:120] + L.wizard_encode(L.WizardFrame(4, 7)) + noise[120:]
        if len(L.WizardParser().feed(data)) >= 2:
            two += 1
            assert L.detect_mode(data) is DeviceMode.WIZARD
    assert two > 100
    # through connect: noise plus one lucky frame from a silent unit is not WIZARD
    dev = TextDevice(bytes(rnd.getrandbits(8) for _ in range(64)).replace(b'U', b'.') + lucky)
    res = connect.run_connect(dev, read_timeout=0.02, settle=0.0, zmodem_timeout=0.05)
    assert res.mode is not DeviceMode.WIZARD
