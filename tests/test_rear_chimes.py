"""Rear Chimes (future firmware LB-RC<n>): detection, level k = 6, routing bit 6,
and stock-firmware parity. Nothing is sent to hardware: the engine tests use a fake
client that only records frames."""
import os

import pytest

from mobridge import dspwrite as D
from mobridge import model as M
from mobridge import rearchimes as RC

STOCK_MASKS = [0x01, 0x02, 0x3e, 0x3d, 0x08, 0x0c, 0, 0x03, 0x10, 0x20, 0x3f, 0]


# --- detection --------------------------------------------------------------
@pytest.mark.parametrize('tag, want', [
    ('LB-01', False), ('LB-02', False), ('LB-03', False), ('LB-04', False), ('LB-12', False),
    ('LB-PAC01', False), ('LB-PAC02', False), ('12:57:20', False), ('TONECAP1', False),
])
def test_detect_by_build_tag(tag, want):
    # a seen tag decides, whatever the version says
    assert RC.detect(tag) is want


def test_detect_only_true_for_lb_rc_tags():
    # LB-RC<n> tags indicate Rear Chimes builds
    assert RC.detect('LB-RC1') is True
    assert RC.detect('LB-RC12') is True
    assert RC.detect('LB-RC99') is True
    # LB-NN (without RC) always False, even for NN >= 3
    assert RC.detect('LB-03') is False
    assert RC.detect('LB-12') is False
    # Missing tag means False
    assert RC.detect(None) is False


def test_build_tag_from_banner_chunks():
    # 0x0401 text arrives in chunks that do not follow line ends
    chunks = ['Application : 02.11.11\r\nBuild Ti', 'me : Jul 17 2026 LB-03\r\n', 'Git SHA ...']
    assert RC.build_tag(chunks) == 'LB-03'
    assert RC.build_tag(['Build Time : Jul 17 2026 12:57:20\n']) == '12:57:20'
    assert RC.build_tag(['Build Time : Jul 17 2026 LB-PAC02']) == 'LB-PAC02'
    assert RC.build_tag(['no banner here']) is None
    assert RC.build_tag(None) is None


# --- wire: level k = 6 and bit 6 ride the existing encoders -----------------
def _tune(masks=STOCK_MASKS):
    root = M.DspModel(fw=210, product=0x202c)
    root.channel_count = 12
    for ch, m in enumerate(masks):
        root.input_map[ch] = m
    root.clear_dirty()
    return root


def test_rear_chimes_level_is_entry_6_and_bit_6_survives_routing_write():
    root = _tune([m | (0x40 if ch in (5, 6) else 0) for ch, m in enumerate(STOCK_MASKS)])
    root.set_input_gain(RC.GAIN_K, -6.0, preset=0)
    root.set_input_map(7, root.input_map[7] | 1 << RC.BIT)
    f = D.flush(root)
    routing = [p for c, p in f.frames if c == 0x0701][0]
    masks = [int.from_bytes(routing[1 + 8 * i:9 + 8 * i], 'big') for i in range(12)]
    assert [ch for ch, m in enumerate(masks) if m & 0x40] == [5, 6, 7]
    gains = [p for c, p in f.frames if c == 0x0704 and p[:2] == b'\x07\x00'][0]
    raw6 = int.from_bytes(gains[2 + 2 * 5:4 + 2 * 5], 'big')   # k = 6 is the 6th entry (k0 = 1)
    assert raw6 == 0xED00 - 6 * 512


def test_stock_firmware_writes_are_byte_for_byte_unchanged():
    """Parity: on stock firmware an ordinary edit sends exactly what it always did
    (routing as read, bit 6 never added; gains entry 6 left at what was read)."""
    root = _tune()
    root.set_input_map(4, 0x09)
    root.set_input_gain(2, -1.5, preset=0)
    root.set_input_gain(3, -2.0, preset=0)
    f = D.flush(root)
    want_routing = b'\x00' + b''.join(m.to_bytes(8, 'big') for m in
                                      [0x01, 0x02, 0x3e, 0x3d, 0x09, 0x0c, 0, 0x03, 0x10, 0x20, 0x3f, 0])
    want_g0 = b'\x07\x00' + bytes.fromhex('ed00ea00e900') + bytes.fromhex('ed00') * 61
    assert f.frames[0] == (0x0701, want_routing)
    assert f.frames[1] == (0x0704, want_g0)
    assert [c for c, _ in f.frames] == [0x0701] + [0x0704] * 4
    assert all(p == b'\x07' + bytes([i]) + bytes.fromhex('ed00') * 64 for i, (_, p) in enumerate(f.frames[2:], 1))


# --- engine (Qt, offscreen) -------------------------------------------------
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@pytest.fixture(scope='module')
def app():
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeClient:
    def __init__(self):
        self.framer = None
        self.sent = []

    def send_frame(self, frame):
        self.sent.append(frame)


def _result(version, lines=None):
    class Result:
        app_version = version
        log_lines = lines or []
    return Result()


def _engine(tmp_path, masks=STOCK_MASKS):
    from mobridge.gui.engine import Engine
    e = Engine()
    e.root = _tune(masks)
    e.client = FakeClient()            # "connected"; frames are only recorded
    return e


@pytest.mark.parametrize('version, lines', [('2.11.10', ['Build Time : Jul 17 2026 LB-PAC02\n']),
                                            ('2.11.8', ['Build Time : Jul 17 2026 12:57:20\n']),
                                            ('2.11.9', [])])
def test_engine_stock_or_older_custom_sends_nothing_and_shows_nothing(app, tmp_path, version, lines):
    e = _engine(tmp_path)
    e._rear_chimes_on_connect(_result(version, lines))
    app.processEvents()
    assert e.rear_chimes is False and e.client.sent == []
    assert e.root.root_flags == 0 and [e.root.input_map[c] for c in range(12)] == STOCK_MASKS


def test_engine_lb03_not_detected_sends_nothing_on_connect(app, tmp_path):
    e = _engine(tmp_path)
    e._rear_chimes_on_connect(_result('2.11.11', ['Build Time : Jul 17 2026 LB-03\n']))
    app.processEvents()
    assert e.rear_chimes is False and e.client.sent == [] and e.root.root_flags == 0


def test_engine_lb_rc1_detected_sends_nothing_on_connect(app, tmp_path):
    e = _engine(tmp_path)
    e._rear_chimes_on_connect(_result('2.11.11', ['Build Time : Jul 17 2026 LB-RC1\n']))
    app.processEvents()
    assert e.rear_chimes is True and e.client.sent == [] and e.root.root_flags == 0


def test_banner_seen_later_turns_detection_on(app, tmp_path):
    e = _engine(tmp_path)
    e._rear_chimes_on_connect(_result('2.11.10'))
    assert not e.rear_chimes
    e._console_text('Build Time : Jul 17 2026 LB-RC1\r\n')
    assert e.rear_chimes


# --- GUI: Channels (CONFIG & MASTER EQ) screen ----------------------------
def _texts(screen, kind):
    return [it.label if kind == 'led' else it.s for it in screen.items
            if hasattr(it, 'label' if kind == 'led' else 's')]


def test_screen_shows_rear_chimes_only_when_detected(app, tmp_path):
    from mobridge.gui.official.screens import ConfigMasterEqScreen
    e = _engine(tmp_path)
    e.root = __import__('mobridge.gui.official.data', fromlist=['x']).demo_model()
    scr = ConfigMasterEqScreen(selected='frontleftwoofer', engine=e)
    scr.rebuild()
    assert RC.NAME not in _texts(scr, 'led') + _texts(scr, 'label')
    assert 'Beeps' in _texts(scr, 'led')
    e.rear_chimes = True
    scr.rebuild()
    assert RC.NAME in _texts(scr, 'led')               # routing dot (bit 6)
    assert RC.NAME in _texts(scr, 'label')             # level spin (k = 6), no volume control
