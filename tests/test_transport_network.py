"""Network ports (rfc2217:// via ser2net on a Raspberry Pi bridge, socket://): open, bytes
both ways, baud change, and a connection that drops mid-session."""
import json
import socket
import threading
import time

import pytest

serial = pytest.importorskip('serial')

from mobridge import session as S
from mobridge.transport import is_network_port, open_transport


class EchoServer:
    """One-client TCP server on localhost. Echoes, records, can hang up."""

    def __init__(self):
        self.ls = socket.socket()
        self.ls.bind(('127.0.0.1', 0)); self.ls.listen(1)
        self.port = self.ls.getsockname()[1]
        self.got = bytearray(); self.conn = None; self.echo = True
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self.conn, _ = self.ls.accept()
        self.ready.set()
        while True:
            try:
                d = self.conn.recv(4096)
            except OSError:
                return
            if not d:
                return
            self.got += d
            if self.echo:
                self.conn.sendall(d)

    def hang_up(self):
        self.ready.wait(2)
        self.conn.shutdown(socket.SHUT_RDWR); self.conn.close(); self.ls.close()


def _read_all(t, n, limit=2.0):
    out = b''; end = time.monotonic() + limit
    while len(out) < n and time.monotonic() < end:
        out += t.read(4096, 0.05)
    return out


def test_url_detection():
    assert is_network_port('rfc2217://192.0.2.10:4000')
    assert is_network_port('socket://localhost:1')
    assert not is_network_port('COM3') and not is_network_port('/dev/ttyUSB0')


def test_open_write_read_and_capture_through_a_url(tmp_path):
    srv = EchoServer()
    cap = tmp_path / 'wire.jsonl'
    t = open_transport('socket://127.0.0.1:%d' % srv.port, capture=str(cap))
    t.write(b'\x7e\x00\x01\x7e')
    assert _read_all(t, 4) == b'\x7e\x00\x01\x7e'
    t.set_baud(230400)                       # socket:// accepts and ignores it
    t.close()
    assert bytes(srv.got) == b'\x7e\x00\x01\x7e'   # exactly the bytes written
    lines = [json.loads(x) for x in cap.read_text().splitlines()]
    assert {'tx', 'rx'} <= {x.get('dir') for x in lines} and any(x.get('baud') == 230400 for x in lines)


def test_rfc2217_baud_change_reaches_the_far_end():
    """A real RFC2217 server (pyserial's PortManager over a loop:// port)."""
    from serial import rfc2217
    ls = socket.socket(); ls.bind(('127.0.0.1', 0)); ls.listen(1)
    port = ls.getsockname()[1]
    loop = serial.serial_for_url('loop://', baudrate=115200, timeout=0.05)

    def serve():
        conn, _ = ls.accept()
        conn.settimeout(0.05)
        class W:                                   # PortManager wants .write()
            write = conn.sendall
        pm = rfc2217.PortManager(loop, W())
        while True:
            try:
                d = conn.recv(1024)
                if not d:
                    return
                loop.write(b''.join(pm.filter(d)))
            except socket.timeout:
                pass
            except OSError:
                return
            back = loop.read(4096)
            if back:
                conn.sendall(b''.join(pm.escape(back)))
    threading.Thread(target=serve, daemon=True).start()

    t = open_transport('rfc2217://127.0.0.1:%d' % port)
    t.set_baud(230400)
    end = time.monotonic() + 3
    while loop.baudrate != 230400 and time.monotonic() < end:
        time.sleep(0.02)
    assert loop.baudrate == 230400
    t.write(b'\x01\x02\xff\x03')                  # 0xff is telnet-escaped and back
    assert _read_all(t, 4) == b'\x01\x02\xff\x03'
    t.close()


def test_dropped_connection_mid_session_is_reported_not_hung():
    srv = EchoServer(); srv.echo = False
    t = open_transport('socket://127.0.0.1:%d' % srv.port)
    lost = []
    s = S.Session(t, keepalive_s=0.05, on_lost=lost.append)
    s.start()
    time.sleep(0.1)
    srv.hang_up()                                  # the Pi lost power
    end = time.monotonic() + 3
    while not lost and time.monotonic() < end:
        time.sleep(0.02)
    s.stop()
    assert lost, 'session did not notice the dropped network port'
    t.close()


def test_engine_unreachable_network_port_goes_disconnected_without_blocking(app):
    from mobridge.gui.engine import Engine
    ls = socket.socket(); ls.bind(('127.0.0.1', 0)); dead = ls.getsockname()[1]; ls.close()
    import threading
    from mobridge import transport as T
    real_open, gate, entered, where = T.open_transport, threading.Event(), threading.Event(), []

    def slow_open(port, baud=None):               # an unreachable Pi: the open hangs first
        where.append(threading.current_thread() is threading.main_thread())
        entered.set()
        gate.wait(30)
        return real_open(port, baud=baud)
    T.open_transport = slow_open
    try:
        e = Engine(); states = []; errors = []
        e.state_changed.connect(states.append); e.error.connect(errors.append)
        e.connect_device('socket://127.0.0.1:%d' % dead)
        # deterministic: connect_device returned while the open still hung, so the
        # UI thread did not wait on the network (no wall-clock limit to tune)
        assert not gate.is_set() and e._net_opening and errors == []
        assert entered.wait(30)                    # the helper thread is inside the open
        gate.set()
    finally:
        T.open_transport = real_open
    assert where == [False]                        # the open ran on the helper thread
    end = time.monotonic() + 30                    # only a deadlock guard
    while not errors and time.monotonic() < end:
        app.processEvents(); time.sleep(0.02)
    assert errors and 14 in states and e.client is None


def test_bridge_scan_finds_a_listener_and_sends_nothing():
    from mobridge import netfind
    ls = socket.socket(); ls.bind(('127.0.0.1', 0)); ls.listen(4)
    port = ls.getsockname()[1]
    got = []

    def acc():
        while True:
            try:
                c, _ = ls.accept()
            except OSError:
                return
            c.settimeout(1)
            try:
                got.append(c.recv(16))           # b'' = closed without sending
            except OSError:
                got.append(None)
            c.close()
    threading.Thread(target=acc, daemon=True).start()
    t0 = time.monotonic()
    hits = netfind.find_bridges(hosts=['127.0.0.1', '127.0.0.2', '127.0.0.3'], port=port, mdns=False)
    assert hits == [('127.0.0.1', 'rfc2217://127.0.0.1:%d' % port)]
    assert time.monotonic() - t0 < 3
    time.sleep(0.2); ls.close()
    assert got and all(g == b'' for g in got)


def test_bridge_scan_can_be_cancelled():
    from mobridge import netfind
    ev = threading.Event(); ev.set()
    assert netfind.find_bridges(cancel=ev, hosts=['127.0.0.1'] * 50, port=1, mdns=False) == []
