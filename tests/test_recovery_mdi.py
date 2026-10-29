"""Recovery of an MDI-bootloader unit (0x202c), against a simulated bootloader that answers
with the bytes BOOTLOADER_DISASM_OCT23.md §5 proves, at the rates it proves.

Proven (disasm of the 2.11.4 bootloader, command table 0xff8035c4 {u16 cmd, handler, needs
session}): 0x0000 (mdimod_general_auth, 0x400016) claims the session, reply [00 01];
0x0001 (0x4000b4, needs session) reply = BE16 product, 00 + 3 version bytes of an L1 record
whose CRC never holds in the bootloader (so 00 00 00 00), BE16 product + 4 version bytes from
the bootloader header at 0x10 (202c / 00 02 0b 04); 0x0002 (0x400182) BE32 baud: a value in
{115200, 230400, 460800} other than the current one -> reply [01] at the old rate, then
switch; same/unsupported -> empty reply, no switch; the UART opens at 115200; a needs-session
command before 0x0000 -> 0x0fff [00 01 00 01]; unknown command -> 0x0fff [00 02 00 02];
0x0201 (0xffa0ba84) -> reply [01], then 0x0200 sub 1 (flash_cb status 0; 0x0200 payload is
BE16 status+1, BE32 arg), ZMODEM into /temp.ini; the received callback sends 0x0200 sub 0,
then fif_flash reports through flash_cb (6 size, 7 position, 8 done, 9 error).
End of the transfer and fif_flash (§5b, proven): ZEOF -> ZRINIT, ZMODEM stops at once (no
ZFIN answer); sub 0, then 2, 6, 7 0, (4, 5 per sector, 7 every 5120 B)..., 8; failures: bad
check sub 9 -5 alone, open failure sub 9 -4 then 8, product mismatch / verify failure (-5/-9)
end in sub 8 with nothing (or not all) written. The ~5 s idle timer (any valid frame resets it,
paused during ZMODEM) unclaims the session and drops the UART to 115200."""
import threading
import time

import pytest

from mobridge import boots, connect, flashflow as F, uirules as R, zmodem
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage
from mobridge.session import Session
from mobridge.transport import Transport
from test_offline import make_fif

AUTH_DATA = bytes.fromhex('0001')                                    # 0x400016
IDENT_DATA = bytes.fromhex('202c00000000202c00020b04')               # 0x4000b4: 0x202c, BL 2.11.4
BAUDS = (115200, 230400, 460800)                                     # switch table 0xff803928
NEEDS_SESSION = {0x0001, 0x0003, 0x0010, 0x0012, 0x0201, 0x0202, 0x0203, 0x0400, 0x0401}


class _Pipe:
    def __init__(self):
        self.buf, self.cv = bytearray(), threading.Condition()

    def put(self, b):
        with self.cv:
            self.buf += b
            self.cv.notify_all()

    def get(self, n, timeout):
        with self.cv:
            if not self.buf and timeout:
                self.cv.wait(timeout)
            out = bytes(self.buf[:n])
            del self.buf[:n]
            return out


class _End(Transport):
    def __init__(self, rx, tx, log):
        self.rx, self.tx, self.log, self.baud = rx, tx, log, connect.POWERON_BAUD

    rewrite = None

    sim = None

    def write(self, data):
        if self.sim is not None and self.baud != self.sim.baud:
            if self.log is not None:
                self.log.append((self.baud, bytes(data)))
            return
        if self.rewrite is not None:
            data = self.rewrite(bytes(data))
        if self.log is not None:
            self.log.append((self.baud, bytes(data)))
        self.tx.put(bytes(data))

    def read(self, size=4096, timeout=0.1):
        return self.rx.get(size, timeout or 0)

    def set_baud(self, baud):
        self.baud = baud

    def flush_input(self):
        with self.rx.cv:
            self.rx.buf.clear()

    def close(self):
        pass


ZRINIT_DEV = zmodem.hex_header(zmodem.ZRINIT, bytes((0x00, 0x28, 0x00, 0x23)))   # 10 KiB, 0x23


def rx_until_eof(dev, timeout=5.0, block_delay=0.0):
    """Device-side ZMODEM receive as both firmwares end it: on ZEOF at the received length,
    ZRINIT and stop (bootloader 0x404dcc -> 0xffa0ad1c, app 2.11.8 0x526e42 -> 0x515118).
    Returns (name, data, bytes read past the ZEOF header: they go to the MDI parser)."""
    rd = zmodem.HeaderReader(dev)
    dev.write(ZRINIT_DEV)
    name, data = '', bytearray()
    while True:
        ftype, b4 = rd.read(timeout)
        crc32 = rd.last_kind == zmodem.ZBIN32
        off = int.from_bytes(b4, 'little')
        if ftype == zmodem.ZRQINIT:
            dev.write(ZRINIT_DEV)
        elif ftype == zmodem.ZFILE:
            info, _ = rd.subpacket(crc32, timeout)
            name, data = info.split(b'\0', 1)[0].decode('latin-1'), bytearray()
            dev.write(zmodem.hex_header(zmodem.ZRPOS, zmodem.pos_bytes(0)))
        elif ftype == zmodem.ZDATA:
            if off != len(data):
                dev.write(zmodem.hex_header(zmodem.ZRPOS, zmodem.pos_bytes(len(data))))
                continue
            while True:
                chunk, end = rd.subpacket(crc32, timeout)
                data += chunk
                time.sleep(block_delay)
                if end in (zmodem.ZCRCQ, zmodem.ZCRCW):
                    dev.write(zmodem.hex_header(zmodem.ZACK, zmodem.pos_bytes(len(data))))
                if end in (zmodem.ZCRCE, zmodem.ZCRCW):
                    break
        elif ftype == zmodem.ZEOF and off == len(data):
            dev.write(ZRINIT_DEV)
            return name, bytes(data), bytes(rd.buf)


def flash_script(size, case='ok', sector=0x10000, erase_s=0.0):
    """fif_flash 0x0200 (sub, arg, delay-before) after the file is in (§5b Q2)."""
    s = [(0, 0, 0)]                                     # received callback 0xffa0b7ce
    if case == 'open':
        return s + [(9, -4, 0), (8, 0, 0)]              # 0xffa08e7a, then sub 8 as well
    s.append((2, 0, 0))                                 # CHECKING FILE
    if case == 'check':
        return s + [(9, -5, 0)]                         # 0xffa08e4a, no sub 8
    if case == 'product':
        return s + [(8, 0, 0)]                          # -5 before any write, sub 8 only
    s += [(6, size, 0), (7, 0, 0)]
    pos = erased = 0
    while pos < size:
        if pos >= erased:
            s += [(4, 0, 0), (5, 0, erase_s)]           # erase between 4 and 5 sends nothing
            erased += sector
        pos = min(pos + 0x1400, size)
        s.append((7, pos, 0))
        if case == 'verify':                            # retries at the same position, then -9
            return s + [(7, pos, 0), (7, pos, 0), (8, 0, 0)]
    return s + [(8, 0, 0)]


class SimMdiBootloader:
    """The 0x202c recovery firmware as seen from the host port. `host` is the host's
    Transport; `wire` records (host baud, bytes) for every host write."""
    authed_at_start = False
    idle_s = 5.0                                        # 0xffa0ab06: 5000 at 100 per tick

    def __init__(self, case='ok', erase_s=0.0, block_delay=0.0):
        a, b = _Pipe(), _Pipe()
        self.wire, self.cmds, self.received, self.progress, self.reverts = [], [], None, [], 0
        self.case, self.erase_s, self.block_delay = case, erase_s, block_delay
        self.host = _End(a, b, self.wire)
        self.host.sim = self                    # bytes sent at a rate the unit isn't on are lost
        self.baud, self.authed = connect.POWERON_BAUD, self.authed_at_start   # UART opens at 115200
        self.dev = _End(b, a, None)
        self.dev_framer = MdiFramer()
        self.last_valid = time.monotonic()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def close(self):
        self._stop.set()
        self.thread.join(5)

    def _reply(self, cmd, data=b''):
        self.dev.write(self.dev_framer.encode(MdiMessage(cmd=cmd, data=data)))

    def _progress(self, sub, arg=0):
        self.progress.append((sub, arg))
        if self.baud != self.host.baud:
            return                                      # sent at a rate the host isn't on
        m = MdiMessage(cmd=0x0200, data=sub.to_bytes(2, 'big') + arg.to_bytes(4, 'big', signed=True))
        self.dev.write(self.dev_framer.encode(m))

    def _flash(self, size):                             # FIF_FLASH task, beside the MDI task
        for sub, arg, delay in flash_script(size, self.case, erase_s=self.erase_s):
            if self._stop.wait(delay):
                return
            self._progress(sub, arg)

    def _tick(self):                                    # 0xffa0aab4 (not called inside ZMODEM)
        if self.idle_s and time.monotonic() - self.last_valid >= self.idle_s:
            self.last_valid = time.monotonic()
            self.authed = False                         # "Session inactive" (0xffa0b0dc)
            if self.baud != connect.POWERON_BAUD:
                self.reverts += 1
            self.baud = connect.POWERON_BAUD            # 0xffa0ab18, session or not

    def _run(self):
        fr = self.dev_framer
        pending = b''
        while not self._stop.is_set():
            got, pending = pending + self.dev.read(4096, 0.05), b''
            self._tick()
            for f in fr.feed(got):
                if f.kind != 'mdi' or f.message is None:
                    continue
                self.last_valid = time.monotonic()      # any complete valid frame (0xffa0aab0)
                cmd = f.message.cmd
                self.cmds.append(cmd)
                data = f.message.data
                if cmd in NEEDS_SESSION and not self.authed:      # PROVEN: dispatcher 0xffa0b462
                    self._reply(0x0fff, bytes.fromhex('00010001'))
                elif cmd == 0x0000:                               # PROVEN: 0x400016
                    self.authed = True
                    self._reply(0x0000, AUTH_DATA)
                elif cmd == 0x0001:                               # PROVEN: 0x4000b4
                    self._reply(0x0001, IDENT_DATA)
                elif cmd == 0x0002:                               # PROVEN: 0x400182
                    want = int.from_bytes(data[:4], 'big') if len(data) >= 4 else None
                    if want in BAUDS and want != self.baud:
                        self._reply(0x0002, b'\x01')               # sent at the old rate,
                        self.baud = want                          # then 0xffa0adfa switches
                    else:
                        self._reply(0x0002)                       # empty, no switch
                elif cmd == 0x0201:                               # PROVEN: 0xffa0ba84
                    self._reply(0x0201, boots.MDI_FLASH_START_REPLY)
                    self._progress(1)                             # flash_cb(status 0) -> sub 1
                    paused = time.monotonic() - self.last_valid  # idle counter paused in ZMODEM
                    try:
                        name, data, pending = rx_until_eof(self.dev, 5.0, self.block_delay)
                    except (zmodem.ZmodemError, TimeoutError):
                        continue                                  # cancelled: no flash
                    self.received = (name, data)
                    self.last_valid = time.monotonic() - paused
                    fr.reset()
                    threading.Thread(target=self._flash, args=(len(data),), daemon=True).start()
                else:                                             # PROVEN: 0xffa0b4b4
                    self._reply(0x0fff, bytes.fromhex('00020002'))


@pytest.fixture
def boot():
    s = SimMdiBootloader()
    yield s
    s.close()


def test_recovery_connect_identifies_and_stops(boot):
    res = connect.run_connect(boot.host, recovery=True, read_timeout=0.3)
    assert res.recovery and res.product_id == 0x202c and res.app_version == '2.11.4'
    assert boot.wire[0][1] == connect.CONNECT_ABORT_SEQ
    # identify only: no serial/dip/debug/config/DspRead frame reached the unit
    assert boot.cmds == [0x0000, 0x0000, 0x0002, 0x0000, 0x0000, 0x0002, 0x0001]
    # probe + SetBaud at 115200, the unit answers [01] and both sides move to 230400
    assert [b for b, _ in boot.wire[:4]] == [115200] * 4 and boot.baud == 230400
    assert {baud for baud, _ in boot.wire[4:]} == {230400}
    assert MdiFramer().encode(MdiMessage(cmd=0x0001, data=b'')) == boots.MDI_IDENTIFY
    assert any(b == boots.MDI_IDENTIFY for _, b in boot.wire)


def test_recovery_flash_reuses_run_flash(boot, tmp_path):
    image = make_fif(version=0x00020B08)
    assert connect.run_connect(boot.host, recovery=True, read_timeout=0.3).recovery
    n = len(boot.cmds)
    s = Session(boot.host, MdiFramer())          # not started: run_flash drives it, as the job does
    st = []
    assert F.run_flash(s, image, 'x.fif', lambda *a: st.append(a))
    assert boot.received == ('x.fif', image)
    assert boot.cmds[n] == 0x0201 and set(boot.cmds[n + 1:]) <= {0x0000}
    assert (230400, boots.MDI_FLASH_START) in boot.wire
    assert st[-1] == ('phase', 10)               # Complete!


def test_recovery_button_path(app, tmp_path, monkeypatch):
    """startRecovery (Lua) -> recovery reconnect -> state 5 -> FIRMWARE RECOVERY ->
    "recovered to" confirm -> the ordinary FlashFile job."""
    from PySide6 import QtCore, QtWidgets
    from mobridge import scripting
    from mobridge.gui.engine import Engine
    from mobridge.gui.official.window import OfficialMainWindow
    from mobridge.modes import DeviceMode

    e = Engine()
    e.port = 'COM9'
    calls = []
    monkeypatch.setattr(e, 'connect_device', lambda port, baud=None: calls.append((port, e._recovery_next)))
    p = tmp_path / 'r.lua'
    p.write_text('fddspconnect.instance():startRecovery()')
    out = []
    assert scripting.ScriptHost(engine=e, log=out.append).run(str(p)), out
    assert calls == [('COM9', True)]

    w = OfficialMainWindow(engine=e)
    res = connect.ConnectResult(product_id=0x202c, app_version='2.11.9', mode=DeviceMode.MDI, recovery=True)

    class _T:
        def write(self, d): pass
        def read(self, n=4096, timeout=0.1): return b''
        def close(self): pass
    e._apply_connect_result(res, _T())
    assert e.conn_state == R.STATE_RECOVERY and e.product_id == 0x202c and not e._recovery_next
    dev = w.stack.widget(w.pages['device'])
    labels = [getattr(i, 'label', None) for i in dev.items]
    assert R.DEVICE_SCREEN_TEXTS['recovery'] in labels and R.DEVICE_SCREEN_TEXTS['update'] not in labels

    fif_path = tmp_path / 'older.fif'
    fif_path.write_bytes(make_fif(version=0x00020B08))   # older than the unit: allowed in Recovery
    Q = QtWidgets.QMessageBox
    asked, flashed = [], []
    monkeypatch.setattr(w, '_fw_ask', lambda icon, text, buttons: (asked.append(text), Q.Ok)[1])
    monkeypatch.setattr(QtCore.QStandardPaths, 'writableLocation', staticmethod(lambda *_a: str(tmp_path)))
    monkeypatch.setattr(e, 'flash_file', lambda plan: flashed.append(plan) or True)
    assert w.firmware_update(str(fif_path))
    assert len(asked) == 1 and 'recovered to version <b>2.11.8</b>' in asked[0]
    assert flashed and flashed[0].code == F.OK
    e._drop_session()


def test_bootloader_refuses_flash_without_session(boot):
    """0x0201 before the 0x0000 handshake gets the 0x0fff session error, never a receive."""
    s = Session(boot.host, MdiFramer())
    assert not F.run_flash(s, make_fif(version=0x00020B08), 'x.fif')
    assert boot.received is None and set(boot.cmds) == {0x0201}


# --- review fixes: no writes in Recovery, nothing MDI on a bootloader link ---------
class _QuietPort:
    """A port that never answers; records every byte the host writes."""

    def __init__(self):
        self.raw = bytearray()

    def write(self, d):
        self.raw += d

    def read(self, n=4096, timeout=0.1):
        if timeout:
            import time
            time.sleep(min(timeout, 0.005))      # block like a real port; never spin
        return b''

    def flush_input(self):
        pass

    def set_baud(self, baud):
        pass

    def close(self):
        pass


def _dirty(e):
    e.root.speaker(0x2000).set_gain(-4.0)


def test_recovery_state_push_sends_no_dspwrite(app):
    from mobridge.gui.engine import Engine
    from mobridge.modes import DeviceMode
    e = Engine()
    _dirty(e)                                    # a stale edit on the old tune
    port = _QuietPort()
    e._apply_connect_result(connect.ConnectResult(product_id=0x202c, app_version='2.11.4',
                                                  mode=DeviceMode.MDI, recovery=True), port)
    try:
        assert e.conn_state == R.STATE_RECOVERY and e.session is not None
        assert not e.metering and not e.root.connected
        jobs = []
        e.session.submit = jobs.append
        _dirty(e)
        e.push('gain')
        assert jobs == [] and not e._pending
    finally:
        e._drop_session()


@pytest.mark.parametrize('mode', ['BOOTS', 'LW', 'WIZARD'])
def test_bootloader_link_starts_no_mdi_session_and_push_sends_nothing(app, mode):
    from mobridge.gui.engine import Engine
    from mobridge.modes import DeviceMode
    e = Engine()
    port = _QuietPort()
    e._apply_connect_result(connect.ConnectResult(product_id=0x2002, mode=DeviceMode[mode]), port)
    try:
        assert e.session is None and not e.metering
        _dirty(e)
        e.push('gain')
        e.rta_request(0)
        assert bytes(port.raw) == b'' and not e._pending
    finally:
        e._drop_session()


def test_boots_recovery_flash_uses_the_boots_job_not_mdi(app, tmp_path, monkeypatch):
    from mobridge.gui.engine import Engine
    from mobridge.modes import DeviceMode
    calls = []

    class FakeBoots:
        def __init__(self, t, policy, log=None):
            calls.append(('init', t))

        def flash(self, path, *, execute=False, expect_product=None):
            calls.append(('flash', path, execute, expect_product))
            return boots.BootsStatus('complete')
    monkeypatch.setattr(boots, 'BootsFlasher', FakeBoots)
    monkeypatch.setattr(F, 'run_flash', lambda *a, **k: calls.append('mdi') or True)
    e = Engine()
    port = _QuietPort()
    e._apply_connect_result(connect.ConnectResult(product_id=0x2002, mode=DeviceMode.BOOTS), port)
    plan = type('P', (), {'path': str(tmp_path / 'x.fif'), 'info': None})()
    assert e.conn_state == R.STATE_RECOVERY and e.flash_file(plan)
    e._boots_thread.join(5)
    assert calls == [('init', port), ('flash', plan.path, True, 0x2002)]
    assert bytes(port.raw) == b''                # nothing MDI went out on the BOOTS link
    e._user_disconnected = True
    e._drop_session()


def test_lw_and_wizard_links_do_not_flash(app, tmp_path):
    from mobridge.gui.engine import Engine
    from mobridge.modes import DeviceMode
    for mode in (DeviceMode.LW, DeviceMode.WIZARD):
        e = Engine()
        port = _QuietPort()
        e._apply_connect_result(connect.ConnectResult(product_id=0x2001, mode=mode), port)
        plan = type('P', (), {'path': str(tmp_path / 'x.fif'), 'info': None})()
        assert not e.flash_file(plan) and bytes(port.raw) == b''
        e._drop_session()


@pytest.mark.parametrize('banner,mode', [(b'', 'MDI'), (b'Prod=0x2002, Ver=2.0.0\r\n', 'BOOTS')])
def test_recovery_connect_never_reaches_configsync(banner, mode):
    from mobridge.modes import DeviceMode
    port = _QuietPort()
    port.read = lambda n=4096, timeout=0.1, _b=bytearray(banner): (bytes(_b), _b.clear())[0]
    res = connect.run_connect(port, recovery=True, read_timeout=0.02, settle=0.0)
    cmds = [f.message.cmd for f in MdiFramer().feed(bytes(port.raw)) if f.kind == 'mdi']
    assert cmds == [0x0000, 0x0000, 0x0002, 0x0000, 0x0000, 0x0002, 0x0001]
    assert res.recovery and res.mode is DeviceMode[mode] and res.model is None
