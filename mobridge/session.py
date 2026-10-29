"""The live MDI session after connect: the official app's service loop.

Mirrors re/analysis/UI_REGION_2.md §1.2 and EXE_FULL_SWEEP_OCT22 (transport):
* one job at a time; every step waits for the reply with the same CMD (or a
  0x0fff failure) before the next step goes out; replies are matched by CMD echo
  only;
* after a model-driven write job the loop sleeps 250 ms (the only coalescing:
  edits made meanwhile wait as dirty bits for the next job);
* keep-alive 0x0001 about once a second while idle;
* unsolicited frames: 0x0401 console text, and 0x0716 [03][p] (the device
  changed preset) is adopted and echoed back as 0x0716 [01][p];
* a transport error ends the session and reports it, so the caller can
  reconnect (the exe reconnects every 100 ms unless the user disconnected).

Everything that goes out still passes parity.assert_frame_cleared, so the
flash family stays locked here too.
"""
from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import parity
from .framing import MdiFramer
from .message import MdiMessage

KEEPALIVE_S = 1.0
POLL_S = 0.01
WRITE_SLEEP_S = 0.25
STEP_TIMEOUT_S = 1.0
FAIL_CMD = 0x0FFF


@dataclass
class Job:
    name: str
    steps: list                                # [(cmd, payload)]
    write: bool = False                        # model-driven write: sleep 250 ms after
    done: Callable | None = None               # done(job) on the session thread
    replies: list = field(default_factory=list)
    failed: list = field(default_factory=list) # indices of steps answered 0x0fff or not at all
    run: Callable | None = None                # custom job body run(session, job) (FlashFile)


class Session:
    def __init__(self, transport, framer: MdiFramer | None = None, *,
                 on_frame: Callable | None = None, on_preset: Callable | None = None,
                 on_lost: Callable | None = None, on_idle: Callable | None = None,
                 log: Callable | None = None, keepalive_s: float = KEEPALIVE_S,
                 step_timeout_s: float = STEP_TIMEOUT_S, write_sleep_s: float = WRITE_SLEEP_S):
        self.t = transport
        self.framer = framer or MdiFramer()
        self.on_frame = on_frame or (lambda m: None)
        self.on_preset = on_preset or (lambda p: None)
        self.on_lost = on_lost or (lambda e: None)
        self.on_idle = on_idle or (lambda: None)
        self.log = log or (lambda s: None)
        self.keepalive_s = keepalive_s
        self.step_timeout_s = step_timeout_s
        self.write_sleep_s = write_sleep_s
        self._q: collections.deque[Job] = collections.deque()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tx = 0.0
        self.busy = False

    # --- control -------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name='mdi-session', daemon=True)
        self._thread.start()

    def stop(self, wait: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(wait)

    def submit(self, job: Job) -> None:
        self._q.append(job)

    @property
    def idle(self) -> bool:
        return not self._q and not self.busy

    # --- loop ----------------------------------------------------------------
    def _send(self, cmd: int, payload: bytes) -> None:
        frame = self.framer.encode(MdiMessage(cmd=cmd, data=payload))
        parity.assert_frame_cleared(frame, cmd, 'session 0x%04x' % cmd)
        self.t.write(frame)
        self._last_tx = time.monotonic()

    def _pump(self, timeout: float, want: int | None = None):
        """Read for up to `timeout`; route unsolicited frames; return the first
        reply whose CMD is `want` (or 0x0fff), else None."""
        deadline = time.monotonic() + timeout
        backlog = self.__dict__.setdefault('_backlog', collections.deque())
        while True:
            # frames parsed after the one returned last time stay queued (back-to-back
            # 0x0200 progress frames can arrive in one read)
            frames = list(backlog) if backlog else self.framer.feed(
                self.t.read(4096, min(POLL_S, max(0.0, deadline - time.monotonic()))))
            backlog.clear()
            for i, fr in enumerate(frames):
                if fr.kind != 'mdi' or fr.message is None:
                    continue
                m = fr.message
                if want is not None and m.cmd in (want, FAIL_CMD):
                    backlog.extend(frames[i + 1:])
                    return m
                self._unsolicited(m)
            if time.monotonic() >= deadline:
                return None

    def _unsolicited(self, m: MdiMessage) -> None:
        if m.cmd == 0x0716 and len(m.data) >= 2 and m.data[0] == 3:
            p = m.data[1]
            self.on_preset(p)
            self._q.appendleft(Job('PresetEcho', [(0x0716, bytes([1, p]))]))
            return
        self.on_frame(m)

    def run_job(self, job: Job) -> Job:
        for i, (cmd, payload) in enumerate(job.steps):
            try:
                self._send(cmd, payload)
            except parity.ParityError as e:          # a held command: refuse this step only
                self.log('%s: %s' % (job.name, e))
                job.replies.append(None); job.failed.append(i)
                continue
            rep = self._pump(self.step_timeout_s, want=cmd)
            job.replies.append(rep)
            if rep is None or rep.cmd == FAIL_CMD:
                job.failed.append(i)
        return job

    def _run(self) -> None:
        self._last_tx = time.monotonic()
        try:
            while not self._stop.is_set():
                if self._q:
                    job = self._q.popleft()
                    self.busy = True
                    if job.run is not None:
                        job.run(self, job)
                    else:
                        self.run_job(job)
                    if job.failed:
                        self.log('%s: %d of %d step(s) failed' % (job.name, len(job.failed), len(job.steps)))
                    if job.done:
                        job.done(job)
                    if job.write:
                        self._pump(self.write_sleep_s)
                    self.busy = False
                    if not self._q:
                        self.on_idle()
                    continue
                if time.monotonic() - self._last_tx >= self.keepalive_s:
                    self._send(0x0001, b'')
                    self._pump(self.step_timeout_s, want=0x0001)
                    continue
                self._pump(POLL_S)
        except Exception as e:                      # port gone, cable pulled
            self.busy = False
            if not self._stop.is_set():
                self.on_lost(e)
