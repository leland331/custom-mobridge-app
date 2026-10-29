from ..qt import QtWidgets
from .base import Screen
from ... import channel_map as cm


class ChannelMapScreen(Screen):
    """ChannelMapForm equivalent (UI_SPEC §3.1 / §Output Mapping). Shows the same
    speaker positions, labels and slot assignment as the official app so it reads
    identically. Changing an assignment is a DSP speakermap write and stays behind
    the parity send-gate."""
    title = 'Channel Map'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Channel Map',
                    "Speaker positions and their amp output channels — same labels as the official app.")

        table = QtWidgets.QTableWidget(len(cm.SPEAKERS), 3)
        table.setHorizontalHeaderLabels(['Speaker position', 'Group', 'Output'])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        table.setSelectionbehavior = getattr(table, 'setSelectionBehavior', None)
        assign = cm.CURRENT_ASSIGNMENT
        for row, s in enumerate(cm.SPEAKERS):
            slot = assign.get(s.object_name)
            out = cm.output_channel_label(slot) if slot else cm.UNASSIGNED
            items = [QtWidgets.QTableWidgetItem(s.display),
                     QtWidgets.QTableWidgetItem(s.group),
                     QtWidgets.QTableWidgetItem(out)]
            if slot:
                items[2].setToolTip('From your current saved configuration')
            for col, it in enumerate(items):
                table.setItem(row, col, it)
        table.resizeColumnsToContents()
        lay.addWidget(table, 1)

        lay.addWidget(self.blocked_note(
            'To re-assign a speaker, use Output Mapping on the CONFIGURATION tab.'))
