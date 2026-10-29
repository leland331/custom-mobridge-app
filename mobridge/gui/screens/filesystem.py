from ..qt import QtWidgets
from .base import Screen


class FilesystemScreen(Screen):
    """UI_SPEC §6: device flash filesystem (DirList/Change/Rm/Flash). Engineering."""
    title = 'Device Files'
    flag = 'mobridgedebug'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Device Files', 'Browse the on-device flash filesystem.')
        self.tree = QtWidgets.QTreeWidget(); self.tree.setHeaderLabels(['Name', 'Size'])
        lay.addWidget(self.tree, 1)
        row = QtWidgets.QHBoxLayout()
        for lbl in ('Refresh', 'Flash file…', 'Delete'):
            row.addWidget(QtWidgets.QPushButton(lbl))
        lay.addLayout(row)
        lay.addWidget(self.pending('Connect a device to browse its files.'))
