from ..qt import QtWidgets
from .base import Screen


class ScriptScreen(Screen):
    """UI_SPEC §9: scripting console. Gated by cfg scripting."""
    title = 'Script'
    flag = 'scripting'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Script', 'Automation console.')
        self.out = QtWidgets.QPlainTextEdit(); self.out.setReadOnly(True)
        lay.addWidget(self.out, 1)
        row = QtWidgets.QHBoxLayout()
        self.input = QtWidgets.QLineEdit(); self.input.setPlaceholderText('script line…')
        row.addWidget(self.input, 1); row.addWidget(QtWidgets.QPushButton('Run'))
        lay.addLayout(row)
        lay.addWidget(self.pending('Type a command and press Enter.'))
