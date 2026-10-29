"""End of a firmware transfer and the fif_flash wait, for both MDI targets.

Both the 0x202c bootloader (BOOTLOADER_DISASM_OCT23.md §5b) and the 2.11.8 app firmware
(ZEOF handler 0x526e42: ZRINIT, then event 10 -> callback 0x515090 -> table 0x4ee278 ->
0x515118 -> 0x525654 stop + 0x51543c file-received) leave ZMODEM right after ZEOF/ZRINIT and
never answer ZFIN. The app sim is the bootloader sim with the session already claimed and no
idle revert modelled (the app firmware's timer is not traced)."""
import pytest

from mobridge import connect, flashflow as F, zmodem
from mobridge.framing import MdiFramer
from mobridge.session import Session
from test_offline import make_fif
from test_recovery_mdi import SimMdiBootloader


class SimAppFirmware(SimMdiBootloader):
    authed_at_start = True
    idle_s = None


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(F, 'FIN_WAIT_S', 0.2)


def _flash(sim, image=None, status=None):
    if isinstance(sim, SimAppFirmware):
        sim.host.baud = sim.baud
    else:
        assert connect.run_connect(sim.host, recovery=True, read_timeout=0.3).recovery
    s = Session(sim.host, MdiFramer())
    st = []
    ok = F.run_flash(s, image or make_fif(version=0x00020B08), 'x.fif', lambda *a: st.append(a))
    return ok, st


@pytest.fixture(params=[SimMdiBootloader, SimAppFirmware], ids=['bootloader', 'app'])
def target(request):
    made = []

    def make(**kw):
        made.append(request.param(**kw))
        return made[-1]
    yield make
    for m in made:
        m.close()


def test_success_status_order_and_no_zfin_wait(target, monkeypatch):
    monkeypatch.setattr(F, 'FIN_WAIT_S', 1.0)           # the real wait still ends in time
    sim = target()
    image = make_fif(version=0x00020B08)
    ok, st = _flash(sim, image)
    assert ok and st[-1] == ('phase', 10) and sim.received == ('x.fif', image)
    subs = [s for s, _ in sim.progress]
    assert subs[:5] == [1, 0, 2, 6, 7] and subs[-1] == 8 and 4 in subs and 5 in subs
    assert ('position', len(image), len(image)) in st and sim.reverts == 0


@pytest.mark.parametrize('case', ['check', 'open'])
def test_sub9_is_an_error_and_stops_at_the_first(target, case):
    sim = target(case=case)
    ok, st = _flash(sim)
    assert not ok and st[-1] == ('phase', 11) and ('phase', 10) not in st


@pytest.mark.parametrize('case', ['product', 'verify'])
def test_sub8_after_failure_only_means_finished(target, case):
    """-5 product mismatch / -9 verify failure end in sub 8 (no sub 9): the job ends as
    finished and the version re-read after the reset is what tells."""
    sim = target(case=case)
    ok, st = _flash(sim)
    assert ok and 9 not in [s for s, _ in sim.progress]


def test_long_flash_needs_the_timer_keepalive(monkeypatch):
    """An erase gap longer than the idle timer: 0x0000 on the timer keeps the link."""
    monkeypatch.setattr(F, 'KEEPALIVE_S', 0.25)
    sim = SimMdiBootloader(erase_s=1.2, block_delay=0.0)
    sim.idle_s = 0.6
    try:
        ok, st = _flash(sim, make_fif(version=0x00020B08))
        assert ok and sim.reverts == 0
    finally:
        sim.close()


def test_without_timer_keepalive_the_link_drops(monkeypatch):
    monkeypatch.setattr(F, 'KEEPALIVE_S', 30.0)
    monkeypatch.setattr(F, 'WAIT_S', 2.0)
    sim = SimMdiBootloader(erase_s=1.2)
    sim.idle_s = 0.6
    try:
        ok, _ = _flash(sim, make_fif(version=0x00020B08))
        assert not ok and sim.reverts == 1
    finally:
        sim.close()


def test_long_zmodem_transfer_is_safe_from_the_idle_timer(monkeypatch):
    """The idle counter is paused during ZMODEM, however long the receive takes."""
    sim = SimMdiBootloader(block_delay=0.15)
    sim.idle_s = 0.6
    monkeypatch.setattr(F, 'KEEPALIVE_S', 0.25)
    image = make_fif(version=0x00020B08, body=__import__("random").Random(1).randbytes(6000))
    try:
        ok, _ = _flash(sim, image)
        assert ok and sim.received[1] == image and sim.reverts == 0
    finally:
        sim.close()


def test_mdi_bytes_read_during_the_zfin_wait_reach_the_framer():
    """Progress frames that arrive with the final ZRINIT are handed on, not dropped, and a
    repeated ZRINIT / ZFIN reply is stripped."""
    from mobridge.message import MdiMessage
    zrinit = zmodem.hex_header(zmodem.ZRINIT, bytes((0, 0x28, 0, 0x23)))
    frame = MdiFramer().encode(MdiMessage(cmd=0x0200, data=bytes(6)))

    class T:
        def __init__(self):
            self.out, self.rx = bytearray(), bytearray()

        def write(self, d):
            self.out += d
            if d.startswith(b'**\x18B0') and d[4:6] == b'0b':            # ZEOF
                self.rx += zrinit + b'*' + frame
            elif b'**\x18B' in d and d[4:6] == b'04' or d[:3] == b'*\x18C' and d[3] == 4:
                self.rx += zmodem.hex_header(zmodem.ZRPOS, zmodem.pos_bytes(0))
            elif d[:2] == b'**' and d[4:6] == b'00':
                self.rx += zrinit

        def read(self, n=4096, timeout=0.1):
            out = bytes(self.rx[:n]); del self.rx[:n]
            return out
    t, extra = T(), bytearray()
    zmodem.send_file(t, b'', 'x', fin_wait=0.05, on_extra=extra.extend)
    assert bytes(extra) == b'*' + frame and t.out.endswith(b'OO')


def test_host_failure_sends_the_abort_sequence():
    class Dead:
        out = bytearray()
        def write(self, d): self.out += d
        def read(self, n=4096, timeout=0.1): return b''
    t = Dead()
    with pytest.raises((zmodem.ZmodemError, TimeoutError)):
        zmodem.send_file(t, b'abc', 'x', timeout=0.05)
    assert t.out.endswith(zmodem.ABORT_SEQ)
