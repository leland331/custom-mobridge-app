"""Device I/O worker: runs the read-only connect off the UI thread.

Kept in its own module so the live-connect path is a single, self-contained piece
that is easy to review, disable, or roll back (project rule: modular, clean, no
extra fallbacks). The protocol itself lives in mobridge.connect; this only moves
it onto a QThread and relays its result/log as Qt signals. It transmits nothing
beyond what connect.run_connect sends, and that is all parity-gated reads.
"""
from __future__ import annotations

from .qt import QtCore, Signal, Slot


class ConnectWorker(QtCore.QObject):
    finished = Signal(object)   # connect.ConnectResult
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, transport, framer=None, read_mask=None, recovery=False):
        super().__init__()
        self._read_mask = read_mask
        self._recovery = recovery
        self._transport = transport
        self._framer = framer

    @Slot()
    def run(self) -> None:
        from .. import connect
        try:
            res = connect.run_connect(self._transport, self._framer, log=self.log.emit,
                                      read_mask=self._read_mask, recovery=self._recovery)
        except Exception as e:                      # surface, never crash the thread
            self.failed.emit('%s: %s' % (type(e).__name__, e))
            return
        self.finished.emit(res)
