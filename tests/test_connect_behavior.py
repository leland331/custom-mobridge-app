"""the reference connect-behaviour rules:

1. On connect the device's tune is the source of truth (engine.root replaced).
2. A change while connected applies live: the model's dirty bits go out at once,
   in the official write order (dspwrite.flush), through the client.
3. Edits made OFFLINE stay in the local model only (saved by File > Save as a
   .rax); nothing is queued, and the next connect replaces them (device wins).
"""
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6.QtWidgets')
from PySide6 import QtWidgets  # noqa: E402

from mobridge import wire_apply  # noqa: E402
from mobridge.gui.engine import Engine  # noqa: E402


@pytest.fixture(scope='module')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeClient:
    """Stands in for a connected device: records frames, never a real port."""
    def __init__(self):
        self.framer = None
        self.sent = []

    def send_frame(self, frame):
        self.sent.append(frame)


def test_offline_edits_stay_in_the_model_and_never_send(app):
    e = Engine()
    assert not e.connected
    s = e.root.speaker(0x2000)                 # Right Sub Woofer (CH4 on the reference device)
    s.set_gain(-3.5)
    f = e.push('gain')
    assert e.client is None                    # no transport ever touched
    assert f.frames and e.root.is_modified()   # the tune carries the edit (File > Save writes it)
    assert e._pending == {}                    # nothing queued for a device


def test_connect_replaces_the_offline_model_device_wins(app):
    from mobridge import model as M
    e = Engine()
    e.root.speaker(0x2000).set_gain(-3.5)
    device_tune = M.DspModel(fw=210, product=0x202c)
    device_tune.map_speaker(0x2000, 3)
    device_tune.speaker(0x2000).gain_db = 0.0
    device_tune.clear_dirty()

    class Result:
        product_id, app_version, info, capabilities, local_id = 0x202c, '2.11.9', {}, 0, None
        channel_gain = {}
        model = device_tune
        def as_values(self): return {'product_id': 0x202c}
    seen = []
    e.model_changed.connect(seen.append)
    e._apply_connect_result(Result(), FakeTransport())
    assert e.root is device_tune and seen == [device_tune]
    assert e.root.speaker(0x2000).gain_db == 0.0   # the offline edit is gone


class FakeTransport:
    def write(self, d): pass
    def read(self, n=4096, timeout=0.1): return b''
    def close(self): pass


def test_connected_change_applies_live_in_official_order(app):
    e = Engine()
    e.client = FakeClient()                    # now "connected"
    assert e.connected
    s = e.root.speaker(0x2000)                 # on CH4 (channel index 3), -3.0 dB on his device
    s.set_gain(-4.0)
    f = e.push('gain')
    # the exact frames the official app sends for a channel gain change [CAP]
    assert e.client.sent == wire_apply.record_frames(4, gain_db=-4.0)
    assert f.cmds() == [0x0700, 0x0702, 0x0708, 0x0704]
    assert e._pending and e.dirty             # live on the device, not yet backed up to a .rax
    assert s.sync == 0                        # dirty bits cleared after the flush
    e.push('nothing')
    assert len(e.client.sent) == 4            # nothing dirty -> nothing sent
