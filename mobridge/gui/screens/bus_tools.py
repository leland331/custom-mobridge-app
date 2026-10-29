from ..qt import QtWidgets
from .base import Screen


class BusToolsScreen(Screen):
    """UI_SPEC §7: CAN / MOST / UDS / A2B engineering panels. All TX is bus-tx."""
    title = 'Bus Tools'
    flag = 'mobridgedebug'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Bus Tools', 'CAN, MOST, UDS and A2B tools. Sending puts traffic on the vehicle bus.')
        lay.addWidget(self.blocked_note(
            'Sending on the vehicle bus is turned off.'))
        tabs = QtWidgets.QTabWidget()
        for name in ('CAN', 'MOST', 'UDS', 'A2B'):
            w = QtWidgets.QWidget(); wl = QtWidgets.QVBoxLayout(w)
            wl.addWidget(self.pending('%s tools are coming soon.' % name))
            wl.addStretch(1)
            tabs.addTab(w, name)
        lay.addWidget(tabs, 1)
