from ...safety import Policy, SafetyError, classify_console
from ..qt import QtWidgets, Qt
from .base import Screen


class DebugConsoleScreen(Screen):
    """UI_SPEC §8: device debug console. Gated by cfg mobridgedebug.

    Our fix for the known flood problem: the log view and the input line are
    separate, the input is checked by the safety filter before anything is sent,
    and the log can be paused/filtered.
    """
    title = 'Debug Console'
    flag = 'mobridgedebug'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        self.policy = Policy.read_only()
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Debug Console', 'Device console.')

        bar = QtWidgets.QHBoxLayout()
        self.pause = QtWidgets.QCheckBox('Pause log')
        self.filter = QtWidgets.QLineEdit(); self.filter.setPlaceholderText('filter (hide lines without this text)')
        self.allow_writes = QtWidgets.QCheckBox('Allow writes')
        # One-click read-only stuck-state capture (fault-capture/CONSOLE_RECIPE.md).
        # Disabled until DebugSend (0x0e00) is byte-matched; the gate reason is the
        # tooltip. Driving the official app meanwhile is fault_capture.py.
        self.diag = QtWidgets.QPushButton('Diagnostic Capture')
        self._refresh_diag_gate()
        self.diag.clicked.connect(self._run_diagnostic)
        bar.addWidget(self.pause); bar.addWidget(self.filter, 1)
        bar.addWidget(self.diag); bar.addWidget(self.allow_writes)
        lay.addLayout(bar)

        self.view = QtWidgets.QPlainTextEdit(); self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)   # bound the WSNDMGR flood
        self.view.setObjectName('consoleLog')
        lay.addWidget(self.view, 1)

        row = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit(); self.input.setPlaceholderText('console command…')
        self.send = QtWidgets.QPushButton('Send')
        self.cls = QtWidgets.QComboBox()   # live safety classification of the typed line
        self.cls.setEnabled(False); self.cls.addItem('')
        row.addWidget(self.input, 1); row.addWidget(self.cls); row.addWidget(self.send)
        lay.addLayout(row)

        engine.log.connect(self._on_log)
        self.input.textChanged.connect(self._reclassify)
        self.input.returnPressed.connect(self._send)
        self.send.clicked.connect(self._send)
        self.allow_writes.toggled.connect(lambda on: setattr(
            self, 'policy', Policy.allow(*__import__('mobridge.commands', fromlist=['Access']).Access) if on
            else Policy.read_only()))

    def _on_log(self, line: str):
        if self.pause.isChecked():
            return
        if self.filter.text() and self.filter.text() not in line:
            return
        self.view.appendPlainText(line)

    def _reclassify(self, text):
        self.cls.clear()
        try:
            self.cls.addItem(classify_console(text).value)
        except SafetyError:
            self.cls.addItem('Not allowed (protects the bootloader)')

    def _send(self):
        line = self.input.text()
        if not line:
            return
        try:
            self.policy.check_console(line)
        except SafetyError as e:
            self.view.appendPlainText('[not sent] %s' % e); return
        if not self.engine.connected:
            self.view.appendPlainText('> %s   [not sent: no device connected]' % line)
        else:
            self.view.appendPlainText('> %s' % line)
            # TODO(device): engine sends via DebugSend on the worker thread
        self.input.clear()

    # --- read-only one-click stuck-state capture --------------------------------
    def _refresh_diag_gate(self):
        from ... import diagnostics
        ok, reason = diagnostics.can_run(self.engine)
        self.diag.setEnabled(ok)
        self.diag.setToolTip(reason or 'Run the read-only stuck-state diagnostic capture.')

    def _run_diagnostic(self):
        from ... import diagnostics
        ok, reason = diagnostics.can_run(self.engine)
        if not ok:
            self.view.appendPlainText('[Diagnostic Capture unavailable] %s' % reason)
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Save diagnostic log', 'mobridge_fault_capture.log', 'Log files (*.log *.txt)')
        if not path:
            return
        try:
            diagnostics.run(self.engine, path)
            self.view.appendPlainText('[Diagnostic Capture] saved to %s' % path)
        except Exception as e:   # SafetyError / NotImplementedError while gated
            self.view.appendPlainText('[Diagnostic Capture] %s' % e)
