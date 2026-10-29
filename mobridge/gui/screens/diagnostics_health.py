"""Diagnostics & Health tab: a live status snapshot + a filterable event feed,
decoded read-only from the CAN frames the Mobridge actually receives.

Layout (project direction): a row of STATUS tiles up top (amp, audio, source,
volume, tone, chimes, vehicle/device network), and a live EVENT FEED below with a
type filter. Everything is observe-only -- it decodes inbound/bus frames through
mobridge.can_events and never transmits, so the parity send-gate does not apply.
Off-filter traffic (doors, locks, ...) the device can't see simply never appears;
there are no placeholder rows.
"""
import os

from ..qt import QtWidgets, Qt
from .base import Screen
from ...can_events import EventFeed, EventType, ChimeCatalog

# Where the reference chime-id -> name annotations persist (a local file, not a device
# write). Lives beside the app config so it survives across sessions.
CHIME_NAMES_PATH = os.path.join(os.path.expanduser('~'), '.mobridge', 'chime_names.json')

# Only these two filter categories get an inline description (project request):
# Chime vs Beep is the one pair that isn't self-explanatory. Keyed by EventType value.
FILTER_HELP = {
    'Chime': 'Chime = the vehicle’s warning/alert sounds (seatbelt, door-ajar, park-aid…), CAN 0x220 / 0x2F4.',
    'Beep':  'Beep = one short acknowledgement beep (e.g. a button press), CAN 0x222 — not a full chime.',
}

# Screen-local styling (scoped to this tab; does not touch the shared theme or the
# official-skin thread's files). Cards for the status tiles, readable tables.
_STYLE = """
#statusTile { background: #1f1f1f; border: 1px solid #343434; border-radius: 6px; }
#tileCaption { color: #8f8f8f; font-size: 11px; letter-spacing: 0.3px; }
#tileValue { color: #f4f4f4; }
QTableWidget { background: #161616; alternate-background-color: #1c1c1c;
               color: #e9e9e9; gridline-color: #2b2b2b; border: 1px solid #333;
               border-radius: 6px; }
QTableWidget::item { padding: 4px 8px; }
QTableWidget::item:selected { background: #3a2422; color: #fff; }
QHeaderView::section { background: #262626; color: #cfcfcf; border: none;
                       border-bottom: 1px solid #3a3a3a; padding: 6px 8px; font-weight: bold; }
"""


class DiagnosticsHealthScreen(Screen):
    title = 'Diagnostics & Health'

    def __init__(self, engine, parent=None, chime_names_path: str = CHIME_NAMES_PATH):
        super().__init__(engine, parent)
        self._chime_names_path = chime_names_path
        catalog = ChimeCatalog()
        catalog.load(chime_names_path)
        self.feed = EventFeed(chimes=catalog)
        self._tiles = {}
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Diagnostics & Health', 'Live vehicle state and events')
        self.setStyleSheet(_STYLE)

        # --- status tiles -------------------------------------------------
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8); grid.setVerticalSpacing(8)
        self._tilespec = [
            ('amp', 'Amplifier'), ('audio', 'Audio'), ('source', 'Source'),
            ('volume', 'Stereo volume'), ('tone', 'Tone (B/M/T)'),
            ('bal', 'Balance / Fade'), ('preset', 'Preset'),
            ('chime', 'Last chime'), ('nm_vehicle', 'Vehicle NM'), ('nm_dev', 'Device NM (0x5C6)'),
            ('a2b', 'A2B link'),
        ]
        for i, (key, label) in enumerate(self._tilespec):
            cell = QtWidgets.QFrame(); cell.setObjectName('statusTile')
            cl = QtWidgets.QVBoxLayout(cell); cl.setContentsMargins(8, 6, 8, 6); cl.setSpacing(1)
            cap = QtWidgets.QLabel(label); cap.setObjectName('tileCaption')
            val = QtWidgets.QLabel('—'); val.setObjectName('tileValue')
            vf = val.font(); vf.setBold(True); vf.setPointSize(vf.pointSize() + 2); val.setFont(vf)
            cl.addWidget(cap); cl.addWidget(val)
            grid.addWidget(cell, i // 6, i % 6)
            self._tiles[key] = val
        lay.addLayout(grid)

        # --- event feed + filter -----------------------------------------
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel('Filter:'))
        self.filter = QtWidgets.QComboBox()
        self.filter.addItem('All events', None)
        for t in EventType:
            self.filter.addItem(t.value, t)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        # Plain-English help, shown beside the box ONLY when Chime or Beep is the
        # current pick (project request) so that one difference is never a guess.
        self.filter_help = QtWidgets.QLabel('')
        self.filter_help.setObjectName('screenSubtitle'); self.filter_help.setWordWrap(True)
        self.clear_btn = QtWidgets.QPushButton('Clear')
        self.clear_btn.clicked.connect(self._clear)
        bar.addWidget(self.filter); bar.addWidget(self.filter_help, 1); bar.addWidget(self.clear_btn)
        lay.addLayout(bar)

        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Time', 'Type', 'Event'])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(0, 90); self.table.setColumnWidth(1, 110)
        lay.addWidget(self.table, 2)

        # --- Observed chimes: build the id -> name map by driving --------------
        cap = QtWidgets.QLabel('Observed chimes — the vehicle sends only a number; '
                               'type the warning you saw on the dash and it is remembered.')
        cap.setObjectName('screenSubtitle'); cap.setWordWrap(True)
        lay.addWidget(cap)
        self.chime_table = QtWidgets.QTableWidget(0, 4)
        self.chime_table.setHorizontalHeaderLabels(['Chime ID', 'Count', 'Last seen', 'Label (editable)'])
        self.chime_table.verticalHeader().setVisible(False)
        self.chime_table.horizontalHeader().setStretchLastSection(True)
        self.chime_table.setAlternatingRowColors(True)
        self.chime_table.setColumnWidth(0, 90); self.chime_table.setColumnWidth(1, 70)
        self.chime_table.setColumnWidth(2, 110)
        self.chime_table.itemChanged.connect(self._on_chime_label_edited)
        lay.addWidget(self.chime_table, 1)

        # Two read-only live sources, no sending either way:
        #  1) PASSIVE log stream: the device streams its debug-console log on connect
        #     (no auth -- NOTES.md) as 0x0401 text, split into lines by the engine's
        #     `device_log` signal.
        #     Live now off the default stream (amp/audio/power/chime lines). Enabling
        #     the richer per-frame CAN channels needs the GATED console path.
        #  2) Raw CAN frames via engine.can_frame, when a listen-only CAN source runs.
        engine.device_log.connect(self.on_log_line)
        if hasattr(engine, 'can_frame'):
            engine.can_frame.connect(self.on_frame)
        self._refresh_tiles()
        self._refresh_chimes()

    # -- live input ---------------------------------------------------------
    def on_frame(self, can_id: int, data: bytes, ts: float = None):
        for ev in self.feed.feed(can_id, bytes(data), ts):
            self._add_event(ev)
        self._refresh_tiles()
        self._refresh_chimes()

    def on_log_line(self, line: str):
        # Passive: parse the device's streamed console log. Sends nothing.
        for ev in self.feed.feed_log_line(line):
            self._add_event(ev)
        self._refresh_tiles()

    # -- rendering ----------------------------------------------------------
    def _add_event(self, ev):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(ev.clock()))
        self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(ev.type.value))
        text = ev.text + ('' if ev.tag == 'C' else '  [%s]' % ev.tag)
        self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(text))
        # store the type's string value (a Qt roundtrip does not preserve enum identity)
        self.table.item(r, 1).setData(Qt.UserRole, ev.type.value)
        self._apply_filter()
        self.table.scrollToBottom()

    def _apply_filter(self, *_):
        want = self.filter.currentData()
        # EventType is a str-enum; Qt may hand back the enum or its bare str value.
        want_val = getattr(want, 'value', want)
        self.filter_help.setText(FILTER_HELP.get(want_val, ''))
        for r in range(self.table.rowCount()):
            et = self.table.item(r, 1).data(Qt.UserRole)
            self.table.setRowHidden(r, want_val is not None and et != want_val)

    def _clear(self):
        self.table.setRowCount(0)

    def _refresh_tiles(self):
        s = self.feed.status

        def dash(v, fmt='%s'):
            return '—' if v is None else fmt % v
        self._tiles['amp'].setText('ENABLED' if s.amp_enabled else ('disabled' if s.amp_enabled is False else '—'))
        self._tiles['audio'].setText('ON' if s.audio_on else ('off' if s.audio_on is False else '—'))
        self._tiles['source'].setText(s.source_name())
        self._tiles['volume'].setText(dash(s.stereo_volume))
        self._tiles['tone'].setText('%s / %s / %s' % (dash(s.bass), dash(s.mid), dash(s.treble)))
        self._tiles['bal'].setText('%s / %s' % (dash(s.balance), dash(s.fade)))
        self._tiles['preset'].setText('—' if s.preset is None else 'Preset %d' % (s.preset + 1))
        self._tiles['chime'].setText('—' if s.last_chime_id is None else self.feed.chimes.label(s.last_chime_id))
        self._tiles['nm_vehicle'].setText('seen' if s.nm_vehicle_seen else '—')
        self._tiles['nm_dev'].setText('seen' if s.nm_device_seen else '—')
        self._tiles['a2b'].setText(dash(s.a2b_link))

    def _refresh_chimes(self):
        import time as _t
        obs = sorted(self.feed.chimes.observed.values(), key=lambda o: o.chime_id)
        self.chime_table.blockSignals(True)   # don't treat our own fills as edits
        self.chime_table.setRowCount(len(obs))
        for r, o in enumerate(obs):
            idcell = QtWidgets.QTableWidgetItem('0x%02X' % o.chime_id)
            idcell.setData(Qt.UserRole, o.chime_id)
            cnt = QtWidgets.QTableWidgetItem(str(o.count))
            seen = QtWidgets.QTableWidgetItem(_t.strftime('%H:%M:%S', _t.localtime(o.last_ts)))
            for it in (idcell, cnt, seen):
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)   # only the label is editable
            lbl = QtWidgets.QTableWidgetItem(self.feed.chimes.name(o.chime_id) or '')
            self.chime_table.setItem(r, 0, idcell)
            self.chime_table.setItem(r, 1, cnt)
            self.chime_table.setItem(r, 2, seen)
            self.chime_table.setItem(r, 3, lbl)
        self.chime_table.blockSignals(False)

    def _on_chime_label_edited(self, item):
        if item.column() != 3:
            return
        id_item = self.chime_table.item(item.row(), 0)
        if id_item is None:
            return
        cid = id_item.data(Qt.UserRole)
        self.feed.chimes.set_name(cid, item.text())
        try:
            self.feed.chimes.save(self._chime_names_path)   # local annotation, not a device write
        except OSError as e:
            self.engine.log.emit('could not save chime names: %s' % e)
        self._refresh_tiles()
