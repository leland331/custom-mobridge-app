"""Every control on the official skin edits the model and sends the official
frames (dspwrite.flush order) through the engine. Driven headless: the canvas
items are poked the way the mouse would (arrow step, LED toggle, slider drag,
combo pick, graph click) against an Engine with a fake connected client."""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')
from PySide6 import QtCore, QtWidgets  # noqa: E402

from mobridge import model as M, wire_apply  # noqa: E402
from mobridge.gui.engine import Engine  # noqa: E402
from mobridge.gui.official.canvas import Spin, Led, Switch, Combo, HSlider, Pill  # noqa: E402
from mobridge.gui.official.components import EqEditor  # noqa: E402
from mobridge.gui.official.screens import (ConfigMasterEqScreen, ChannelEqScreen, TimeAlignScreen,  # noqa: E402
                                           LimiterScreen)


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


def engine():
    e = Engine()
    e.client = FakeClient()
    return e


def items(screen, cls, active=None):
    out = [i for i in screen.items if isinstance(i, cls)]
    return [i for i in out if active is None or getattr(i, 'active', getattr(i, 'enabled', True)) == active]


def test_config_screen_controls(app):
    e = engine()
    scr = ConfigMasterEqScreen(engine=e, selected='rightsubwoofer')
    root = e.root
    root.routing_read = True        # as after a successful 0x0711 read on connect (test_routing_gate)
    # input gain spin (Stereo): one arrow step = 0.1 dB -> 0x0704 sub7 on every preset
    spins = items(scr, Spin, True)
    stereo = spins[0]
    assert stereo.number == 0.0
    stereo.step(+1)
    assert root.input_gains[0][1] == 0.1
    assert cmds(e.client.sent) == [0x0704] * 4 and e.client.sent[0][5:7] == b'\x07\x00'
    e.client.sent.clear()
    # input LED: Right off on the sub -> one routing write 0x0701 (ROOT_INPUT_MAP)
    scr = ConfigMasterEqScreen(engine=e, selected='rightsubwoofer')
    right = [l for l in items(scr, Led) if l.label == 'Right'][0]
    assert right.on
    right.click(Ev(right.cx, right.cy))
    assert root.input_map[3] == 0x01
    assert cmds(e.client.sent) == [0x0701]
    e.client.sent.clear()
    # mono delay slider: drag to the middle -> 5.0 ms -> 0x0704 sub8 per preset
    scr = ConfigMasterEqScreen(engine=e, selected='rightsubwoofer')
    sl = items(scr, HSlider)[0]
    mid = QtCore.QPointF(sl.x0 + 8 + (sl.x1 - sl.x0 - 16) * 0.5, sl.cy)
    sl.click(Ev(mid.x(), mid.y())); sl.release(mid)
    assert root.input_delay_ms[0] == 5.0
    assert e.client.sent[0][5:9] == b'\x08\x00' + (5000).to_bytes(2, 'big')
    e.client.sent.clear()
    # master EQ: click on the graph adds a point -> 0x0702 ch0 + 0x0704 sub6
    scr = ConfigMasterEqScreen(engine=e, selected='rightsubwoofer')
    ed = items(scr, EqEditor)[0]
    gr = ed._graph_rect()
    ed.click(Ev(gr.center().x(), gr.center().y()))
    assert len(root.preset(0).master.eq.points) == 1
    assert cmds(e.client.sent) == [0x0702, 0x0704]
    e.client.sent.clear()
    # output mapping: unmap CH4 -> routing + sourcesel + chan props
    scr = ConfigMasterEqScreen(engine=e, selected='rightsubwoofer')
    combo = items(scr, Combo, True)[0]
    combo.select('No Mapping')
    assert root.channel_for_role(0x2000) == -1
    assert 0x0701 in cmds(e.client.sent)


def test_channel_eq_screen_controls(app):
    e = engine()
    root = e.root
    s = root.speaker(0x2000)
    scr = ChannelEqScreen(engine=e, selected='rightsubwoofer')
    # MUTE switch in the header: exactly the captured record frames
    sw = items(scr, Switch)[0]
    sw.click(Ev(sw.r.center().x(), sw.r.center().y()))
    assert s.mute
    assert e.client.sent == wire_apply.record_frames(4, gain_db=-3.0, mute=True)[:1]   # lone 0x0700 (capture)
    e.client.sent.clear()
    # filter type -> Low Pass: 0x0704 sub3 (+ bins/biquads for a mode-2 channel)
    scr = ChannelEqScreen(engine=e, selected='rightsubwoofer')
    items(scr, Combo)[0].select('Low Pass')
    assert s.filter_type == M.FT_LP
    assert 0x0704 in cmds(e.client.sent) and any(f[5] == 3 for f in e.client.sent if f[3:5] == b'\x07\x04')
    e.client.sent.clear()
    # Lo-P Hz spin is now active; one step up = 151 Hz
    scr = ChannelEqScreen(engine=e, selected='rightsubwoofer')
    lop = [sp for sp in items(scr, Spin, True) if sp.value.endswith('Hz')][0]
    lop.step(+1)
    assert s.f1 == 151
    e.client.sent.clear()
    # invert phase LED
    scr = ChannelEqScreen(engine=e, selected='rightsubwoofer')
    inv = [l for l in items(scr, Led) if l.label == 'Invert Phase'][0]
    inv.click(Ev(inv.cx, inv.cy))
    assert s.invert_phase and e.client.sent[0][3:5] == b'\x07\x00' and e.client.sent[0][9] == 1
    e.client.sent.clear()
    # gain fader is locked while the channel is muted (FUN_140080d10)
    scr = ChannelEqScreen(engine=e, selected='rightsubwoofer')
    ed = items(scr, EqEditor)[0]
    fr = ed._fader_rect()
    ed.click(Ev(fr.center().x(), fr.top() + fr.height() * 0.25)); ed.release(QtCore.QPointF(0, 0))
    assert s.gain_db == -3.0 and not e.client.sent
    s.set_mute(False); e.push('unmute'); e.client.sent.clear()
    # gain fader drag -> 0x0700 record first
    scr = ChannelEqScreen(engine=e, selected='rightsubwoofer')
    ed = items(scr, EqEditor)[0]
    ed.click(Ev(fr.center().x(), fr.top() + fr.height() * 0.25)); ed.release(QtCore.QPointF(0, 0))
    assert s.gain_db == ed.gain_db and 0.0 < s.gain_db <= 9.0
    assert cmds(e.client.sent)[0] == 0x0700


def test_time_align_and_limiter_screens(app):
    e = engine()
    root = e.root
    ta = TimeAlignScreen(engine=e)
    dist = items(ta, Spin, True)
    dist[0].step(+1)                                   # first row distance 0.1 cm
    first = root.speaker(root.channel_role[0])
    assert first.distance == 0.1
    e.client.sent.clear()
    ta = TimeAlignScreen(engine=e)
    calc = [p for p in items(ta, Pill) if p.label.startswith('CALC')][0]
    calc.click(Ev(calc.r.center().x(), calc.r.center().y()))
    pr = root.preset(0)
    assert pr.timealign.furthest == 0.1 and first.delay_ms == 0.0
    others = [s for s in root.speakers_in_preset(0) if s is not first]
    assert all(abs(s.delay_ms - 0.1 * M.MS_PER_CM) < 1e-9 for s in others)
    assert 0x0707 in cmds(e.client.sent) and 0x0700 in cmds(e.client.sent)
    e.client.sent.clear()
    # limiter slider: drag to the left end = -36 dBFS on that row
    lim = LimiterScreen(engine=e)
    sl = items(lim, HSlider)[0]
    sl.click(Ev(sl.x0 + 8, sl.cy)); sl.release(QtCore.QPointF(sl.x0 + 8, sl.cy))
    assert first.limit_dbfs == -36.0
    rec = [f for f in e.client.sent if f[3:5] == b'\x07\x00'][0]
    assert rec[5 + 12:5 + 16] != b'\x7f\xff\xff\xff'
    # units switch -> inches, 0x0707 sub3 with units 1
    e.client.sent.clear()
    ta = TimeAlignScreen(engine=e)
    metric = items(ta, Switch)[0]
    metric.click(Ev(metric.r.center().x(), metric.r.center().y()))
    assert root.preset(0).timealign.units == 1
    t = [f for f in e.client.sent if f[3:5] == b'\x07\x07'][0]
    assert t[5:8] == b'\x03\x00\x01'


def test_offline_screens_edit_the_demo_model_without_an_engine(app):
    scr = ConfigMasterEqScreen(engine=None, selected='rightsubwoofer')
    sp = items(scr, Spin, True)[0]
    sp.step(-1)
    assert scr.root.input_gains[0][1] == -0.1 and scr.root.is_modified()


def test_copy_eq_to_all_marks_every_other_channel(app):
    e = engine()
    root = e.root
    src = root.speaker(0x2000)
    src.eq.add(100.0, -3.0, fw=210)
    scr = ChannelEqScreen(engine=e, selected='rightsubwoofer')
    scr._auto_copy = lambda menu: next(a for a in menu.actions() if a.text() == 'Copy To All')
    e.client.sent.clear()
    scr.eq_handler(src, 'copy eq')('copy')
    others = [s for s in root.speakers_in_preset(root.current_preset) if s is not src]
    assert others and all([(p.freq, p.gain) for p in s.eq.points] == [(100.0, -3.0)] for s in others)
    assert 0x0704 in cmds(e.client.sent)
