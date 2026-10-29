"""Live routing writes (0x0701 carries the WHOLE map) only from a map read by 0x0711
on this connection: before that the input dots are disabled and no 0x0701 goes out,
so one click can no longer send unread outputs as 0 (PR #38 review)."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')
from PySide6 import QtCore, QtWidgets  # noqa: E402

from mobridge import devread, dspwrite as D, model as M, rearchimes as RC  # noqa: E402
from mobridge.gui.engine import Engine  # noqa: E402
from mobridge.gui.official import data  # noqa: E402
from mobridge.gui.official.canvas import Combo, Led  # noqa: E402
from mobridge.gui.official.screens import ConfigMasterEqScreen  # noqa: E402

STOCK = [0x01, 0x02, 0x3e, 0x3d, 0x08, 0x0c, 0, 0x03, 0x10, 0x20, 0x3f, 0]


@pytest.fixture(scope='module')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeClient:
    framer = None

    def __init__(self):
        self.sent = []

    def send_frame(self, frame):
        self.sent.append(frame)


class Ev:
    def __init__(self, x, y):
        self._p = QtCore.QPointF(x, y)

    def position(self):
        return self._p


def cmds(frames):
    return [int.from_bytes(f[3:5], 'big') for f in frames]


def _engine(read: bool, rear=False):
    e = Engine()
    e.root = data.demo_model()
    e.root.routing_read = read
    e.client = FakeClient()
    e.rear_chimes = rear
    return e


def _dots(scr):
    return {i.label: i for i in scr.items if isinstance(i, Led)}


# --- devread: the flag follows the 0x0711 reply ---------------------------
class _Rep:
    def __init__(self, d):
        self.data = d


def _read(reply):
    def exchange(cmd, payload):
        return _Rep(reply) if cmd == 0x0711 and reply is not None else None
    return devread.read_tune(exchange, M.DspModel(fw=210), product_id=0x202c)[0]


def test_full_routing_read_sets_flag():
    r = _read(b''.join(m.to_bytes(8, 'big') for m in STOCK))
    assert r.routing_read and [r.input_map[c] for c in range(12)] == STOCK


@pytest.mark.parametrize('reply', [None, b''.join(m.to_bytes(8, 'big') for m in STOCK[:7])])
def test_missing_or_short_routing_read_leaves_flag_off(reply):
    assert _read(reply).routing_read is False


# --- flush / engine: no 0x0701 without the read ----------------------------
def test_flush_hold_routing_drops_only_0x0701():
    root = data.demo_model()
    root.set_input_map(4, 0x09)
    f = D.flush(root, clear=False, hold_routing=True)
    assert 0x0701 not in f.cmds() and any('0x0701' in s for s in f.skipped)
    assert D.flush(root).cmds()[0] == 0x0701       # default unchanged (offline, .rax, tests)


def test_output_mapping_without_read_sends_no_routing(app):
    e = _engine(read=False)
    scr = ConfigMasterEqScreen(engine=e, selected='rightsubwoofer')
    [c for c in scr.items if isinstance(c, Combo) and c.active][0].select('No Mapping')
    assert e.client.sent and 0x0701 not in cmds(e.client.sent)


# --- screens.py click path ---------------------------------------------------
def test_dots_disabled_and_inert_until_routing_read(app):
    e = _engine(read=False, rear=True)
    before = dict(e.root.input_map)
    scr = ConfigMasterEqScreen(engine=e, selected='frontleftwoofer')
    dots = _dots(scr)
    assert RC.NAME in dots and not any(d.enabled for d in dots.values())
    for d in dots.values():
        assert d.click(Ev(d.cx, d.cy)) is False
    assert e.client.sent == [] and e.root.input_map == before and e.root.root_flags == 0


def test_dots_live_offline():
    # offline nothing is sent (the device's map replaces the model on connect)
    e = Engine()
    e.root = data.demo_model()
    scr = ConfigMasterEqScreen(engine=e, selected='frontleftwoofer')
    assert all(d.enabled for d in _dots(scr).values())


def test_rear_chimes_click_keeps_the_read_map(app):
    e = _engine(read=True, rear=True)
    root = e.root
    for c, m in enumerate(STOCK):          # the map as read by 0x0711 on this connection
        root.input_map[c] = m
    ch = root.channel_for_role(data.role_for('frontleftwoofer'))
    scr = ConfigMasterEqScreen(engine=e, selected='frontleftwoofer')
    rc = _dots(scr)[RC.NAME]
    assert rc.enabled and not rc.on
    rc.click(Ev(rc.cx, rc.cy))
    assert cmds(e.client.sent) == [0x0701]
    want = [m | (1 << RC.BIT if c == ch else 0) for c, m in enumerate(STOCK)]
    assert D.routing_payload(root) in e.client.sent[0]     # all 12 read masks, one bit changed
    assert [root.input_map[c] for c in range(12)] == want
    # a second dot on the rebuilt screen keeps the first click (mask read at click time)
    e.client.sent.clear()
    scr = ConfigMasterEqScreen(engine=e, selected='frontleftwoofer')
    beeps = _dots(scr)['Beeps']
    beeps.click(Ev(beeps.cx, beeps.cy))
    assert root.input_map[ch] == want[ch] ^ (1 << M.INPUT_BITS['Beeps'])
    assert all(root.input_map[c] == want[c] for c in range(12) if c != ch)


# --- engine.meters12 detection ----------------------------------------------
class _Result:
    def __init__(self, lines):
        self.app_version = '2.12.0'
        self.log_lines = lines


@pytest.mark.parametrize('lines, want', [
    (['Build Time : Jul 17 2026 LB-RC1\n'], True),
    (['Build Time : Jul 17 2026 LB-06\n'], False),
    (['Build Time : Jul 17 2026 12:57:20\n'], False),
    ([], False)])
def test_meters12_follows_build_tag(app, lines, want):
    e = _engine(read=True)
    e._rear_chimes_on_connect(_Result(lines))
    assert e.meters12 is want and e.rear_chimes is want and e.client.sent == []


def test_meters12_turns_on_with_late_banner(app):
    e = _engine(read=True)
    e._rear_chimes_on_connect(_Result([]))
    assert e.meters12 is False
    e._console_text('Build Time : Jul 17 2026 LB-RC1\r\n')
    assert e.meters12 is True
