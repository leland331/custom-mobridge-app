from ..qt import QtWidgets
from .base import Screen


class ConfigurationScreen(Screen):
    """UI_SPEC §4: config files (.rax), NVM export, factory reset."""
    title = 'Configuration'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Configuration', 'Back up and restore device settings; export NVM.')

        def act(label, cls, cb=None):
            b = QtWidgets.QPushButton(label)
            b.setToolTip('safety class: %s' % cls)
            if cb:
                b.clicked.connect(cb)
            else:
                b.clicked.connect(lambda: self._gated(label))
            return b

        # Only the safe, everyday config actions the official app has: back up /
        # restore a .rax tune and export the NVM partitions for inspection. The
        # destructive "Clear NVM / Factory reset" is deliberately NOT offered here
        # -- a factory wipe isn't something this app should make one click away.
        # There is no "Sync" button: the official app applies every change live
        # and persists it to the device immediately; a .rax backup is the only
        # "save". A manual sync has no equivalent in the official app, so it's gone.
        grid = QtWidgets.QGridLayout()
        grid.addWidget(act('Save config from device (.rax)', 'read'), 0, 0)
        grid.addWidget(act('Load config file to device', 'write'), 0, 1)
        grid.addWidget(act('Export NVM (FIXED / APP)', 'read'), 1, 0)
        box = QtWidgets.QGroupBox('Actions'); box.setLayout(grid)
        lay.addWidget(box)
        lay.addStretch(1)

    def _gated(self, what):
        QtWidgets.QMessageBox.information(self, what,
            '%s needs a connected device.' % what)
