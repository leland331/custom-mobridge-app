"""Connection screen and engine connect guards (offscreen Qt; no device, no network)."""
import pytest


@pytest.fixture
def engine(app, monkeypatch):
    from mobridge.gui.engine import Engine
    e = Engine()
    calls = []
    monkeypatch.setattr(e, 'connect_device', lambda port, baud=None: calls.append(port))
    e.calls = calls
    return e


def _screen(engine, monkeypatch, tmp_path, ports):
    from mobridge.gui.config import Options
    from mobridge.gui.screens.connection import ConnectionScreen
    from mobridge import netfind
    monkeypatch.setattr('mobridge.transport.list_ports', lambda: ports)
    monkeypatch.setattr(netfind, 'find_bridges', lambda cancel=None, **k: [])
    return ConnectionScreen(engine, options=Options(str(tmp_path / 'cfg.ini')))


def test_usb_mobridge_is_connected_automatically(engine, monkeypatch, tmp_path):
    s = _screen(engine, monkeypatch, tmp_path, [('COM1', 'Bluetooth', False), ('COM3', 'FTDI', True)])
    assert engine.calls == ['COM3']
    assert s.port.currentData() == 'COM3'


def test_no_usb_searches_network_and_says_so(engine, monkeypatch, tmp_path, app):
    s = _screen(engine, monkeypatch, tmp_path, [])
    s._on_found([])                                    # finder thread result, delivered directly
    assert engine.calls == []
    assert s.status.text() == 'Not connected. No Mobridge found over USB or on the network.'
    assert s.connect_btn.isEnabled() and s.connect_btn.text() == 'Connect'


def test_device_box_filled_from_engine_info_and_cleared_on_disconnect(engine, monkeypatch, tmp_path):
    from mobridge.modes import DeviceMode
    s = _screen(engine, monkeypatch, tmp_path, [])
    s._find_cancel = None
    engine.device_info.emit({'product': 0x202c, 'version': '2.11.14', 'info': {}, 'serial': '0011223344556677'})
    assert (s.f_ver.text(), s.f_prod.text(), s.f_serial.text()) == ('2.11.14', '0x202c', '0011223344556677')
    assert s.f_name.text()                             # product name from products.BY_ID
    engine.set_mode(DeviceMode.DISCONNECTED)
    assert s.f_ver.text() == s.f_serial.text() == ''


def test_connect_device_twice_on_same_port_is_ignored(app):
    from mobridge.gui.engine import Engine
    e = Engine()
    e.client = object()                                # already connected
    e.port = 'COM3'
    e.connect_device('COM3')                           # would otherwise open COM3 a second time
    assert e.client is not None and e.port == 'COM3'


def test_features_reset_and_rear_chimes_alias(app):
    from mobridge.gui.engine import Engine
    e = Engine()
    assert e.features == frozenset() and not e.rear_chimes
    e.rear_chimes = True
    assert e.features == {'rear_chimes'}
    e.client = object()
    e._features_from_banner('Build Time : Jul 17 2026 LB-06')
    assert not e.rear_chimes
    assert e.compensation is not None and e.compensation.ok


def test_usb_reconnect_opens_off_the_ui_thread(app, monkeypatch):
    import threading
    import time
    from mobridge.gui.engine import Engine
    seen, gate = [], threading.Event()

    def fake_open(port, baud=None):
        seen.append(threading.current_thread() is threading.main_thread())
        gate.wait(30)                                  # the open hangs until the test lets go
        raise OSError('unplugged')
    monkeypatch.setattr('mobridge.transport.open_transport', fake_open)
    e = Engine()
    e._reconnecting = True
    e.connect_device('COM3')
    # deterministic, no wall-clock limit: connect_device came back while the open
    # was still hanging, so the UI thread never waited on it
    assert not gate.is_set() and e._net_opening
    gate.set()
    end = time.time() + 30                             # only a deadlock guard
    while e._net_opening and time.time() < end:
        app.processEvents(); time.sleep(0.01)
    e._user_disconnected = True                        # stop further retries
    assert seen and seen[0] is False                   # opened on the helper thread
    assert not e._net_opening
