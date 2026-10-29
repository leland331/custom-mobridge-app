from ..qt import QtWidgets
from .base import Screen


class LimiterScreen(Screen):
    """Vendor LimiterScreen / LimiterWidget."""
    title = 'Limiter'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Limiter', 'Output limiter.')
        lay.addWidget(self.pending('Limits are set on the LIMITER tab.'))
        lay.addStretch(1)


class MasternodeScreen(Screen):
    """Vendor MasternodeScreen — A2B master node config. Device-gated."""
    title = 'Masternode'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Masternode', 'A2B master-node configuration (reachable with a device connected).')
        lay.addWidget(self.pending('Shown when a master-node device is connected.'))
        lay.addStretch(1)
