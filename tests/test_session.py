"""Session loop: reply wait, keep-alive, preset echo, write sleep, loss."""
import time

from mobridge import session as S
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage
from mobridge.transport import Transport


class Dev(Transport):
    def __init__(self, answer=True):
        self.fr = MdiFramer(); self.out = bytearray(); self.sent = []; self.answer = answer; self.dead = False

    def push(self, cmd, data):
        self.out += MdiFramer().encode(MdiMessage(cmd=cmd, data=data))

    def write(self, data):
        if self.dead:
            raise OSError('port gone')
        for f in self.fr.feed(data):
            if f.kind == 'mdi' and f.message:
                self.sent.append((f.message.cmd, bytes(f.message.data), time.monotonic()))
                if self.answer:
                    self.push(f.message.cmd, b'\x01')

    def read(self, size=4096, timeout=0.1):
        if self.dead:
            raise OSError('port gone')
        if not self.out:
            time.sleep(min(timeout, 0.005))
        d = bytes(self.out[:size]); del self.out[:size]
        return d


def test_job_steps_wait_for_their_reply_and_failures_are_counted():
    d = Dev(answer=False)
    s = S.Session(d, step_timeout_s=0.05)
    j = s.run_job(S.Job('w', [(0x0700, b'\x00\x01'), (0x0704, b'\x05\x00\x01')]))
    assert j.failed == [0, 1] and [c for c, _d, _t in d.sent] == [0x0700, 0x0704]
    d.answer = True
    j = s.run_job(S.Job('w', [(0x0700, b'\x00\x01')]))
    assert j.failed == [] and j.replies[0].cmd == 0x0700


def test_keepalive_preset_echo_and_write_sleep():
    d = Dev()
    presets = []
    idle = []
    s = S.Session(d, keepalive_s=0.05, on_preset=presets.append, on_idle=lambda: idle.append(1),
                  write_sleep_s=0.1)
    s.start()
    try:
        time.sleep(0.2)
        assert any(c == 0x0001 for c, _d, _t in d.sent)          # keep-alive while idle
        d.push(0x0716, b'\x03\x02')                               # the device changed preset
        time.sleep(0.1)
        assert presets == [2] and (0x0716, b'\x01\x02') in [(c, x) for c, x, _t in d.sent]
        s.keepalive_s = 10
        s.submit(S.Job('a', [(0x0700, b'\x00\x01')], write=True))
        s.submit(S.Job('b', [(0x0700, b'\x00\x02')], write=True))
        time.sleep(0.4)
        t = [t for c, x, t in d.sent if c == 0x0700]
        assert len(t) == 2 and t[1] - t[0] >= 0.09                # 250 ms (here 100) after each write job
        assert idle
    finally:
        s.stop()


def test_lost_port_is_reported():
    d = Dev()
    lost = []
    s = S.Session(d, keepalive_s=0.02, on_lost=lost.append)
    s.start()
    d.dead = True
    time.sleep(0.15)
    s.stop()
    assert lost and isinstance(lost[0], OSError)


def test_rta_frame_and_reply_follow_the_firmware():
    from mobridge import cmdspec
    assert cmdspec.dsp_test(7, 1 << 3) == b'\x07\x08'            # CH4
    assert cmdspec.parse_dsp_test(b'\x07\x08\xec', 7, 8) == {'db': [-20]}
