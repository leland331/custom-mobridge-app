import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)


import pytest


@pytest.fixture(autouse=True)
def _reset_gain_unity():
    """The gain/EQ unity reference is a firmware-gated module global; reset it to
    the default (the reference >v207 unit, 0xED00) before each test so the fw-version
    switch can never leak across tests."""
    from mobridge import encoders
    encoders.set_gain_unity_for_fw(208)
    yield
    encoders.set_gain_unity_for_fw(208)


@pytest.fixture
def app():
    """Shared offscreen QApplication for GUI tests. Skips if Qt is unavailable."""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    pytest.importorskip('PySide6', reason='Qt binding not installed')
    from mobridge.gui.ui import build_app
    a, _ = build_app([])
    return a


@pytest.fixture(autouse=True)
def _no_lan_discovery(monkeypatch):
    """Tests never browse mDNS or scan the real LAN for a network bridge."""
    from mobridge import netfind
    monkeypatch.setattr(netfind, "browse_mdns", lambda *a, **k: [])
    monkeypatch.setattr(netfind, "subnet_hosts", lambda: [])


@pytest.fixture(autouse=True)
def _stop_leaked_sessions(monkeypatch):
    """A test that connects an Engine to a fake leaves its mdi-session thread
    running; fakes answer read() at once, so a leftover thread spins and starves
    every later test (the full suite once stalled at ~81%). Stop every
    session a test started when it ends."""
    from mobridge import session as S
    started = []
    orig = S.Session.start

    def start(self):
        started.append(self)
        orig(self)
    monkeypatch.setattr(S.Session, 'start', start)
    yield
    for s in started:
        s.stop(wait=2.0)
    leaked = [s for s in started if s._thread is not None and s._thread.is_alive()]
    if leaked:
        pytest.fail('%d mdi-session thread(s) still running after stop(): %s'
                    % (len(leaked), ', '.join(s._thread.name for s in leaked)), pytrace=False)
