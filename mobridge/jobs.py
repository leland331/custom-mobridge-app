"""Job model, mirroring the app's FDP job queue (FDP_EV_NEWJOB / CANCELJOB).

A job = one MDI request and the response(s) it waits for. The runner owns the
transport while a job runs, routes any unrelated frames (debug log lines,
unsolicited messages) to `on_frame`, and enforces the safety policy before a
single byte is encoded.
"""
from __future__ import annotations

import itertools
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from . import commands
from .framing import Frame, Framer, FramingUnknown
from .message import MdiMessage
from .modes import FdpEvent
from . import parity
from .safety import Policy
from .transport import Transport

WIREMAP_PATH = os.path.join(os.path.dirname(__file__), 'wiremap.json')


class JobState(Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class JobError(RuntimeError):
    pass


@dataclass
class Job:
    command: commands.Command
    data: bytes = b''
    timeout: float = 2.0
    id: int = field(default_factory=itertools.count(1).__next__)
    state: JobState = JobState.PENDING
    response: MdiMessage | None = None
    error: str | None = None


def load_wiremap(path: str = WIREMAP_PATH) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


class JobRunner:
    def __init__(self, transport: Transport, framer: Framer, policy: Policy | None = None,
                 wiremap: dict | None = None,
                 on_frame: Callable[[Frame], None] | None = None,
                 on_event: Callable[[FdpEvent, Job], None] | None = None,
                 require_parity: bool = True):
        self.t = transport
        self.framer = framer
        self.policy = policy or Policy.read_only()
        self.require_parity = require_parity   # never transmit a non-capture-verified command
        self.wiremap = wiremap if wiremap is not None else load_wiremap()
        self.on_frame = on_frame or (lambda f: None)
        self.on_event = on_event or (lambda e, j: None)
        self.session: int | None = None

    def build(self, job: Job) -> MdiMessage:
        w = self.wiremap.get('jobs', {}).get(job.command.name)
        if not w:
            raise FramingUnknown('no wire mapping for %s in wiremap.json' % job.command.name)
        if 'cmd' in w and w['cmd'] is not None:          # flat MDI command code (FRAMING.md)
            return MdiMessage(cmd=w['cmd'], data=job.data, session=self.session)
        missing = [k for k in ('fblock', 'inst', 'fkt', 'op') if w.get(k) is None]
        if missing or self.wiremap.get('devid') is None:
            raise FramingUnknown('no wire mapping for %s yet (missing %s in wiremap.json)'
                                 % (job.command.name, ', '.join(missing or ['devid'])))
        return MdiMessage(self.wiremap['devid'], w['fblock'], w['inst'], w['fkt'], w['op'], job.data,
                          session=self.session)

    def _matches(self, job: Job, req: MdiMessage, msg: MdiMessage) -> bool:
        w = self.wiremap['jobs'][job.command.name]
        if req.cmd is not None:                          # flat command-code style
            resp = w.get('resp_cmd')
            return msg.cmd == (resp if resp is not None else req.cmd)
        resp_op = w.get('resp_op')
        return (msg.fblock == req.fblock and msg.fkt == req.fkt
                and (resp_op is None or msg.op == resp_op))

    def run(self, job: Job) -> MdiMessage:
        self.policy.check_command(job.command)       # before anything is encoded
        req = self.build(job)
        wire = self.framer.encode(req)
        if self.require_parity:
            # Cleared to send = byte-verified frame OR an authorised non-persisting
            # command (connect/read). Every write stays gated.
            parity.assert_frame_cleared(wire, req.cmd, job.command.name)
        job.state = JobState.RUNNING
        self.on_event(FdpEvent.NEWJOB, job)
        self.t.write(wire)
        deadline = time.monotonic() + job.timeout
        while time.monotonic() < deadline:
            for fr in self.framer.feed(self.t.read(4096, 0.05)):
                if fr.kind == 'mdi' and fr.message and self._matches(job, req, fr.message):
                    job.response = fr.message
                    job.state = JobState.DONE
                    return fr.message
                if fr.kind == 'error':
                    job.state, job.error = JobState.FAILED, fr.text
                    raise JobError('%s failed: %s' % (job.command.name, fr.text))
                self.on_frame(fr)
        job.state, job.error = JobState.FAILED, 'timeout'
        self.on_event(FdpEvent.CANCELJOB, job)
        raise TimeoutError('%s: no response in %.1fs' % (job.command.name, job.timeout))

    def call(self, name: str, data: bytes = b'', timeout: float = 2.0) -> MdiMessage:
        return self.run(Job(commands.get(name), data, timeout))
