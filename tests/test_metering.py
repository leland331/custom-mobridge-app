"""Selected-channel level meter over DspTest 0x0719 type 7 (the Limiter RTA read)."""
import os

import pytest

from mobridge import metering


def _engine():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    pytest.importorskip('PySide6.QtWidgets')
    from PySide6 import QtWidgets
    from mobridge.gui.engine import Engine
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app, Engine()


class _Session:
    def __init__(self):
        self._q = []
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)


def _reply(job, data):
    from mobridge.framing import MdiMessage
    job.replies.append(MdiMessage(cmd=0x0719, data=data))
    job.done(job)


def test_fraction_clamps():
    assert metering._frac(0.0, -60.0) == 1.0
    assert metering._frac(6.0, -60.0) == 1.0
    assert metering._frac(-100.0, -60.0) == 0.0
    assert metering._frac(None, -60.0) == 0.0


def test_poll_sends_only_the_rta_read_for_outputs_1_to_8():
    app, e = _engine()
    e.session = _Session()
    e.enable_metering(True, channel=4)
    e._meter_poll()
    (job,) = e.session.jobs
    assert job.steps == [(0x0719, bytes([0x07, 0x08]))]     # [type 7][mask bit 3] = output 4
    assert not job.write
    for ch in (9, 10, 11, 12):                               # u8 mask: no read exists
        e.set_meter_channel(ch)
        e._meter_poll()
    assert len(e.session.jobs) == 1
    e.session = None


def test_reply_shades_only_the_selected_channel_and_peak_holds():
    app, e = _engine()
    e.session = _Session()
    seen = []
    e.meter_levels_changed.connect(seen.append)
    e.enable_metering(True, channel=1)
    e._meter_poll()
    _reply(e.session.jobs[-1], bytes([0x07, 0x01, (-6) & 0xFF]))
    assert seen[-1][1].dbfs == -6.0
    e._meter_poll()
    _reply(e.session.jobs[-1], bytes([0x07, 0x01, (-30) & 0xFF]))
    r = seen[-1][1]
    assert r.dbfs == -30.0 and r.peak_dbfs == -6.0
    e.set_meter_channel(2)
    assert seen[-1] == {}
    e.rta_level.emit(0, -3.0)                                # a level for another output is ignored
    assert seen[-1] == {}
    e.session = None


def test_meter_stops_when_the_session_drops():
    app, e = _engine()
    e.enable_metering(True)
    assert e.metering and e._meter_timer.isActive()
    e._drop_session()
    assert not e.metering and not e._meter_timer.isActive()


def test_rta_byte_decode_silence_and_impossible_values():
    # firmware: int8(20*log10(rms) + 3); silence (-inf) saturates to a 0x00 byte
    assert metering.rta_dbfs(-12) == -12.0
    assert metering.rta_dbfs(3) == 3.0
    assert metering.rta_dbfs(0) == float('-inf')
    assert metering.rta_dbfs(4) is None and metering.rta_dbfs(127) is None


def test_silence_or_bad_reply_never_pegs_the_bar_red():
    app, e = _engine()
    e.session = _Session()
    seen = []
    e.meter_levels_changed.connect(seen.append)
    e.enable_metering(True, channel=1)
    e._meter_poll()
    _reply(e.session.jobs[-1], bytes([0x07, 0x01, 0x00]))           # no signal -> 0x00
    assert seen[-1][1].fraction() == 0.0
    n = len(seen)
    for bad in (bytes([0x07, 0x01, 0x40]),                            # +64 dB: not a level
                bytes([0x07, 0x01]) + bytes(20)):                      # longer than its mask
        e._meter_poll()
        _reply(e.session.jobs[-1], bad)
    assert len(seen) == n
    e.session = None


def test_meter_shades_the_selected_channel_fader_end_to_end():
    """Real window + engine: a level for the selected channel shades its gain fader."""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    import pytest
    pytest.importorskip('PySide6.QtWidgets')
    from PySide6 import QtWidgets
    from mobridge.gui.engine import Engine
    from mobridge.gui.official.window import OfficialMainWindow
    from mobridge.gui.official.screens import ConfigMasterEqScreen
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    e = Engine()
    w = OfficialMainWindow([], engine=e)
    cfg = next(w.stack.widget(w.pages[i]) for i in range(5)
               if isinstance(w.stack.widget(w.pages[i]), ConfigMasterEqScreen))
    assert cfg.selected == 'rightsubwoofer'      # channel 4 by default
    assert cfg.eq.level_frac is None             # no meter until a reading arrives
    # the screen already registered ch4 as the monitored channel on build
    assert e._meter_channel == 4
    e.enable_metering(True)
    e.rta_level.emit(3, -6.0)
    app.processEvents()
    assert cfg.eq.level_frac is not None and cfg.eq.level_frac > 0.0
    w.close()
