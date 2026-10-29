"""MasternodeScreen (stacked index 6, FUN_14008f000; MASTERNODE_SCREEN.md).

Reachable only for products with flag bit 10 (the master-node family). Three
inner pages switched by checkable buttons: Controls (GeneralTestWidget), Logging
(LogWidget) and Levels (LevelTestWidget). Logging is receive-only and shows the
A2B / CAN bus events. Controls that would send a frame whose byte layout is not
pinned from the exe stay disabled (parity safety rule)."""
from __future__ import annotations

from ..qt import QtCore, QtWidgets
from ... import luabus

TITLE = 'MASTERNODE'
TOOLTIP = 'Masternode Tab (Ctrl+T, Ctrl+M)'
NAV = ('Controls', 'Logging', 'Levels')
UDS_VEHICLES = ['volvo', 'saab', 'bmw-nonmp3', 'saabrear', 'freelander', 'lamborghini', 'mb-agw',
                'audimmi3g-ext', 'volvogen2', 'landrovergen2', 'astonmartin2premium', 'volvogen2premium']
LOG_COLUMNS = ['Network', 'Time', 'Addr', 'Param', 'Disasm', 'Target Node']
LEVELS = ['Volume', 'Bass (dB)', 'Mid (dB)', 'Treble (dB)', 'Fade', 'Balance', 'Subwoofer', 'Input Gain']

QSS = """
QWidget { color: #d1d1d1; font-family: 'Droid Sans'; }
QWidget#menuWidget { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #a3a3a3, stop:0.5 #858585, stop:1 #a3a3a3); }
QPushButton { background: #343434; border: 1px solid #555; padding: 6px; }
QPushButton:checked { background: #D12323; }
QPushButton:disabled { color: #777; }
QTableWidget, QLineEdit, QComboBox, QSpinBox, QTabWidget::pane { background: #2d2d2d; }
QHeaderView::section { background: #343434; color: #d1d1d1; }
"""


def _hex_combo(pattern):
    c = QtWidgets.QComboBox()
    c.setEditable(True)
    c.setValidator(_rx_validator(pattern))
    return c


def _rx_validator(pattern):
    from ..qt import QtGui
    return QtGui.QRegularExpressionValidator(QtCore.QRegularExpression(pattern))


BYTE_RX = '^([0-9a-fA-F]{2})$'
BYTES_RX = '^([0-9a-fA-F]{2})*$'


# UDS routines (B3): [SID] + data; RoutineControl 0x31 data = [sub][0xF0][type][params]
def uds_diag_session() -> bytes:
    return bytes([0x10, 0x02])


def uds_chan_start(st) -> bytes:
    f = st['freq'] & 0xFFFF
    return bytes([0x31, 0x01, 0xF0, 0x01, st['chan'] & 0xFF, f >> 8, f & 0xFF, st['amp'] & 0xFF])


def uds_stop(kind: int) -> bytes:
    return bytes([0x31, 0x02, 0xF0, kind])


def uds_gpio_start(st) -> bytes:
    m, o = st['gpio_mask'] & 0xFFFF, st['gpio_out'] & 0xFFFF
    return bytes([0x31, 0x01, 0xF0, 0x02, m >> 8, m & 0xFF, o >> 8, o & 0xFF])


def uds_freq(sub: int, src: int) -> bytes:
    return bytes([0x31, sub, 0xF0, 0x03, src & 0xFF])


class ControlsPage(QtWidgets.QWidget):
    """GeneralTestWidget (FUN_1400bd990): protocolTabs, tabs 1-4 off until a
    network is detected."""

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        lay = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName('protocolTabs')
        lay.addWidget(self.tabs)
        # tab 0 General
        g = QtWidgets.QWidget(); gl = QtWidgets.QHBoxLayout(g)
        self.btnReset = QtWidgets.QPushButton('Reset Board'); self.btnReset.setObjectName('btnReset')
        self.btnReset.clicked.connect(self._reset_board)
        self.btnClearNvm = QtWidgets.QPushButton('Clear NVM'); self.btnClearNvm.setObjectName('btnClearNvm')
        self.btnClearNvm.setEnabled(False)
        gl.addWidget(self.btnReset); gl.addWidget(self.btnClearNvm); gl.addStretch(1)
        self.tabs.addTab(g, 'General')
        # tab 1 MOST
        m = QtWidgets.QWidget(); ml = QtWidgets.QVBoxLayout(m)
        common = QtWidgets.QGroupBox('Common Messages'); cl = QtWidgets.QHBoxLayout(common)
        for t in (' Get FBlockIDs', 'Deallocate All', 'Connect Sink 1', 'Connect Sink 2'):
            cl.addWidget(QtWidgets.QPushButton(t))
        custom = QtWidgets.QGroupBox('Custom Message'); xl = QtWidgets.QGridLayout(custom)
        self.most_fields = []
        for i, h in enumerate(('Target Addr', 'FBlock', 'Inst ID', 'FID', 'OpTL', 'Params')):
            xl.addWidget(QtWidgets.QLabel(h), 0, i)
            cb = _hex_combo(BYTES_RX if h == 'Params' else BYTE_RX); self.most_fields.append(cb)
            xl.addWidget(cb, 1, i)
        self._btn_grid(xl, 1, 6, self.most_custom_send)
        ml.addWidget(common); ml.addWidget(custom); ml.addStretch(1)
        self.tabs.addTab(m, 'most')
        # tab 2 A2B
        a = QtWidgets.QWidget(); al = QtWidgets.QGridLayout(a)
        al.addWidget(QtWidgets.QLabel('A2B Network Configuration .XML'), 0, 0)
        al.addWidget(QtWidgets.QPushButton('Flash'), 0, 1)
        self.a2b_fields = []
        for i, h in enumerate(('Target Node', 'NetworkId', 'Data')):
            al.addWidget(QtWidgets.QLabel(h), 1, i)
            cb = _hex_combo(BYTES_RX if h == 'Data' else BYTE_RX); self.a2b_fields.append(cb)
            al.addWidget(cb, 2, i)
        self._btn_grid(al, 2, 3, self.a2b_custom_send)
        self.tabs.addTab(a, 'a2b')
        # tab 3 CAN
        c = QtWidgets.QWidget(); cg = QtWidgets.QGridLayout(c)
        cg.addWidget(QtWidgets.QLabel('Baudrate:'), 0, 0)
        baud = QtWidgets.QComboBox(); baud.addItems(['100k', '125k', '250k', '500k', '800k'])
        cg.addWidget(baud, 0, 1)
        self.can_iface = _hex_combo(BYTE_RX); self.can_id = _hex_combo(BYTES_RX); self.can_data = _hex_combo(BYTES_RX)
        cg.addWidget(QtWidgets.QLabel('Interface'), 1, 0); cg.addWidget(self.can_iface, 1, 1)
        cg.addWidget(QtWidgets.QLabel('ID'), 2, 0); cg.addWidget(self.can_id, 2, 1)
        cg.addWidget(QtWidgets.QLabel('Data'), 3, 0); cg.addWidget(self.can_data, 3, 1)
        self._btn_grid(cg, 3, 2, self.can_custom_send)
        self.can_buf = {'interface': 0, 'id': 0x3C3}        # FUN_1400bf020 template
        self.tabs.addTab(c, 'can')
        # tab 4 UDS (MASTERNODE_SCREEN.md B3): routine state lives on the widget
        self.uds = {'chan': 0, 'freq': 0, 'amp': 0, 'gpio_mask': 0, 'gpio_out': 0, 'src': 0}
        u = QtWidgets.QWidget(); ug = QtWidgets.QGridLayout(u)
        gen = QtWidgets.QGroupBox('GENERAL'); gnl = QtWidgets.QHBoxLayout(gen)
        self.uds_vehicle = QtWidgets.QComboBox(); self.uds_vehicle.addItems(UDS_VEHICLES); gnl.addWidget(self.uds_vehicle)
        self._btn(gnl, 'Diag Session Req', lambda: self.uds_send(uds_diag_session()))
        ch = QtWidgets.QGroupBox('CHANNEL OUTPUT'); chl = QtWidgets.QGridLayout(ch)
        fr = QtWidgets.QSpinBox(); fr.setRange(0, 0xFFFF); fr.valueChanged.connect(lambda v: self.uds.__setitem__('freq', v))
        am = QtWidgets.QSpinBox(); am.setRange(0, 0xFF); am.valueChanged.connect(lambda v: self.uds.__setitem__('amp', v))
        chl.addWidget(QtWidgets.QLabel('Frequency:'), 0, 0); chl.addWidget(fr, 0, 1)
        chl.addWidget(QtWidgets.QLabel('Amplitude:'), 1, 0); chl.addWidget(am, 1, 1)
        for i, (t, bit) in enumerate((('Front Right', 0x02), ('Front Left', 0x08), ('Rear Right', 0x10),
                                      ('Rear Left', 0x20), ('Subwoofer', 0x01), ('Front Center', 0x04))):
            cb = QtWidgets.QCheckBox(t)
            cb.toggled.connect(lambda on, b=bit: self._bit('chan', b, on))
            chl.addWidget(cb, 2 + i // 3, i % 3)
        row = QtWidgets.QHBoxLayout(); chl.addLayout(row, 4, 0, 1, 3)
        self._btn(row, 'Chan OutTest Start', lambda: self.uds_send(uds_chan_start(self.uds)))
        self._btn(row, 'Chan OutTest Stop', lambda: self.uds_send(uds_stop(1)))
        gp = QtWidgets.QGroupBox('GPIO'); gpl = QtWidgets.QGridLayout(gp)
        for i in range(5):
            cb = QtWidgets.QCheckBox('GPIO%d' % (i + 1))
            cb.toggled.connect(lambda on, b=0x100 << i: self._bit('gpio_mask', b, on))
            gpl.addWidget(cb, 0, i)
        om = QtWidgets.QSpinBox(); om.setRange(0, 0xFFFF); om.valueChanged.connect(lambda v: self.uds.__setitem__('gpio_out', v))
        gpl.addWidget(QtWidgets.QLabel('Output Mask:'), 1, 0); gpl.addWidget(om, 1, 1)
        row = QtWidgets.QHBoxLayout(); gpl.addLayout(row, 2, 0, 1, 5)
        self._btn(row, 'GPIO Test Start', lambda: self.uds_send(uds_gpio_start(self.uds)))
        self._btn(row, 'GPIO Test Stop', lambda: self.uds_send(uds_stop(2)))
        fs = QtWidgets.QGroupBox('FREQUENCY SPECTRUM'); fsl = QtWidgets.QGridLayout(fs)
        row = QtWidgets.QHBoxLayout(); fsl.addLayout(row, 0, 0, 1, 3)
        self._btn(row, 'Freq Spec Test Start', lambda: self.uds_send(uds_freq(0x01, self.uds['src'])))
        self._btn(row, 'Freq Spec Test Results', lambda: self.uds_send(uds_freq(0x03, self.uds['src'])))
        grp = QtWidgets.QButtonGroup(fs)
        for i, (t, v) in enumerate((('Output A', 0x0A), ('Input B (MOST)', 0x1B), ('Input C (Analog)', 0x2C))):
            b = QtWidgets.QPushButton(t); b.setCheckable(True); grp.addButton(b)
            b.clicked.connect(lambda _c=False, v=v: self.uds.__setitem__('src', v))
            fsl.addWidget(b, 1, i)
        ug.addWidget(gen, 0, 0); ug.addWidget(ch, 1, 0); ug.addWidget(gp, 0, 1); ug.addWidget(fs, 1, 1)
        self.tabs.addTab(u, 'uds')
        for i in range(1, 5):
            self.tabs.setTabEnabled(i, False)

    @staticmethod
    def _btn(layout, text, fn):
        b = QtWidgets.QPushButton(text); b.clicked.connect(lambda _c=False: fn()); layout.addWidget(b)
        return b

    def _btn_grid(self, grid, r, c, fn):
        b = QtWidgets.QPushButton('Send'); b.clicked.connect(lambda _c=False: fn()); grid.addWidget(b, r, c)

    @staticmethod
    def _hex(cb) -> bytes:
        t = ''.join(cb.currentText().replace('0x', '').split())
        return bytes.fromhex(t if len(t) % 2 == 0 else '0' + t) if t else b''

    def _int(self, cb) -> int:
        return int.from_bytes(self._hex(cb), 'big')

    def most_custom_send(self):
        """MOST Custom Message -> 0x0621 (FUN_140181b40), fields in combo order."""
        a, fb, inst, fid, op, par = self.most_fields
        cmd, pl = luabus.most_send({'dest': self._int(a), 'fblockid': self._int(fb), 'instid': self._int(inst),
                                    'fktid': self._int(fid), 'operation': self._int(op)}, self._hex(par))
        return self._send(cmd, pl)

    def a2b_custom_send(self):
        """A2B Send -> 0x0645 (FUN_1401825f0): [u16 node][u8 networkId][u16 len][data]."""
        n, nid, d = self.a2b_fields
        cmd, pl = luabus.a2b_send({'targetnode': self._int(n), 'fktid': self._int(nid)}, self._hex(d))
        return self._send(cmd, pl)

    def can_custom_send(self):
        """CAN Send (FUN_1400c2b30) -> 0x0610 (FUN_140181660, B10.2):
        u16 +0x3c (0) | u8 interface | u32 id | <= 8 data bytes. Interface 0..2 and
        id 0..0xFFF are only stored when valid (FUN_1400bf5e0 / FUN_1400bf500)."""
        if self.can_iface.currentText().strip():
            v = self._int(self.can_iface)
            if 0 <= v <= 2:
                self.can_buf['interface'] = v
        if self.can_id.currentText().strip():
            v = self._int(self.can_id)
            if 0 <= v <= 0xFFF:
                self.can_buf['id'] = v
        data = self._hex(self.can_data)[:8]
        pl = luabus.be(0, 2) + luabus.be(self.can_buf['interface'], 1) + luabus.be(self.can_buf['id'], 4) + data
        return self._send(0x0610, pl)

    def _send(self, cmd, pl):
        if self.engine is not None and getattr(self.engine, 'connected', False):
            self.engine.bus_request_async(cmd, pl, lambda rep: None)
        return cmd, pl

    def _bit(self, key, bit, on):
        self.uds[key] = (self.uds[key] | bit) if on else (self.uds[key] & ~bit)

    def uds_send(self, payload: bytes):
        """UdsMsgSend 0x0650 [SID][data] on the live session."""
        if self.engine is not None and getattr(self.engine, 'connected', False):
            self.engine.bus_request_async(0x0650, payload, lambda rep: None)
        return payload

    def _reset_board(self):
        if self.engine is not None:
            self.engine.restart()


class LoggingPage(QtWidgets.QWidget):
    """LogWidget (FUN_1400b73c0): receive-only per-bus tables."""

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        bar = QtWidgets.QHBoxLayout()
        self.clearBtn = QtWidgets.QPushButton('Clear Log'); self.clearBtn.clicked.connect(self.clear)
        self.pauseBtn = QtWidgets.QPushButton('Pause'); self.pauseBtn.setCheckable(True)
        bar.addWidget(self.clearBtn); bar.addWidget(self.pauseBtn); bar.addStretch(1)
        lay.addLayout(bar)
        self.tabs = QtWidgets.QTabWidget(); lay.addWidget(self.tabs)
        self.tables, self.search = {}, {}
        for key, label in (('most', 'MOST'), ('a2b', 'A2B'), ('can', 'CAN'), ('uds', 'UDS')):
            w = QtWidgets.QWidget(); wl = QtWidgets.QVBoxLayout(w)
            s = QtWidgets.QLineEdit(); s.setPlaceholderText('Search')
            t = QtWidgets.QTableWidget(0, len(LOG_COLUMNS)); t.setHorizontalHeaderLabels(LOG_COLUMNS)
            t.horizontalHeader().setStretchLastSection(True)
            s.textChanged.connect(lambda txt, k=key: self._filter(k, txt))
            wl.addWidget(s); wl.addWidget(t)
            self.tables[key], self.search[key] = t, s
            self.tabs.addTab(w, label)
        self.tabs.setCurrentIndex(3)
        if engine is not None:
            engine.bus_event.connect(self.add_event)

    def add_event(self, ev):
        if self.pauseBtn.isChecked():
            return
        t = self.tables.get(ev.bus)
        if t is None:
            return
        f = ev.fields or {}
        addr = '%04X' % f['id'] if 'id' in f else ''
        param = (f.get('data') or ev.raw).hex(' ').upper()
        node = '' if ev.sub is None else str(ev.sub)
        row = t.rowCount(); t.insertRow(row)
        for i, v in enumerate((ev.bus.upper(), QtCore.QTime.currentTime().toString('hh:mm:ss.zzz'),
                               addr, param, '', node)):
            t.setItem(row, i, QtWidgets.QTableWidgetItem(v))
        self._filter(ev.bus, self.search[ev.bus].text())

    def _filter(self, key, text):
        t = self.tables[key]
        for r in range(t.rowCount()):
            hit = not text or any(text.lower() in (t.item(r, c).text().lower() if t.item(r, c) else '')
                                  for c in range(t.columnCount()))
            t.setRowHidden(r, not hit)

    def clear(self):
        for t in self.tables.values():
            t.setRowCount(0)


# Levels (MASTERNODE_SCREEN.md B7/B8): (label, objectName, model field, min, max,
#   net-A MOST fktid (FBlock 0x22 inst 1) or None, net-B code byte 2)
LEVEL_SLIDERS = [
    ('Volume', 'volumeSlider', 'volume', 0, 255, 0x0400, 0),
    ('Bass (dB)', 'bassSlider', 'bass', -39, 39, 0x0202, 1),
    ('Mid (dB)', 'midSlider', 'mid', -39, 39, None, 3),
    ('Treble (dB)', 'trebleSlider', 'treble', -39, 39, 0x0203, 2),
    ('Fade', 'fadeSlider', 'fade', -15, 15, 0x0204, 5),
    ('Balance', 'balanceSlider', 'balance', -15, 15, 0x0200, 4),
    ('Subwoofer', 'subGainSlider', 'subgain', -27, 27, 0x0402, 6),
    ('Input Gain', 'inputGainVolume', None, 0, 255, None, None),
]


def level_net_a(fktid: int, value: int, volume: bool = False) -> tuple[int, bytes]:
    """Net A: the MOST object {u16 0x0401, u32 CODE 0xFFFF0122, u8 2, u16 1, u8 val} copied
    verbatim and serialised by FUN_140181b40 (MASTERNODE_SCREEN B10.2) -> 0x0621
    04 01 | 22 | 01 | fid | 02 | 00 | 00 01 | val. Volume goes as int(val * 100.0 / 255.0)."""
    from ... import luabus
    v = int((value & 0xFF) * 100.0 / 255.0) if volume else value & 0xFF
    return luabus.most_send({'dest': 0x0401, 'fblockid': 0x22, 'instid': 0x01, 'fktid': fktid,
                             'operation': 0x02}, bytes([v]))


def level_net_b(slave: int, idx: int, value: int) -> tuple[int, bytes]:
    """Net B: A2B object {node=slave, 0x22, u32 0x0100XX05 LE, u16 1 LE, val} read by the
    QFdpA2b worker as {u16 node, u8 fktid, u16 len@4, data@6} (B10.1-B10.3) -> 0x0645
    node | 22 | 00 05 | idx 01 01 00 val (bass, node 0x0102, 7 = 01 02 22 00 05 01 01 01 00 07)."""
    from ... import luabus
    code = 0x01000005 | (idx << 16)
    raw = code.to_bytes(4, 'little') + (1).to_bytes(2, 'little') + bytes([value & 0xFF])
    return 0x0645, luabus.be(slave, 2) + bytes([0x22]) + raw[0:2][::-1] + raw[2:]


class LevelsPage(QtWidgets.QWidget):
    """LevelTestWidget (FUN_1400cbd20): "DSP Control" sliders. Master (+0x38 = 1) writes
    the head-unit model (0x0705); Slave (+0x38 = 2) sends function-block messages to
    the slave address on network A (MOST) or B (A2B)."""

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.mode = 1
        self.network = 0
        self.slave = 0
        lay = QtWidgets.QVBoxLayout(self)
        box = QtWidgets.QGroupBox('DSP Control'); bl = QtWidgets.QGridLayout(box)
        self.sliders = {}
        for i, (label, obj, field, lo, hi, fa, fb) in enumerate(LEVEL_SLIDERS):
            bl.addWidget(QtWidgets.QLabel(label), 0, i, alignment=Qt_center())
            s = QtWidgets.QSlider(QtCore.Qt.Vertical); s.setObjectName(obj); s.setRange(lo, hi)
            s.valueChanged.connect(lambda v, n=i: self.on_slider(n, v))
            bl.addWidget(s, 1, i, alignment=Qt_center())
            self.sliders[label] = s
        bl.addWidget(QtWidgets.QLabel('100%'), 2, 0, alignment=Qt_center())
        row = QtWidgets.QHBoxLayout()
        self.ms_switch = QtWidgets.QCheckBox('Master/Slave')
        self.ms_switch.toggled.connect(self.on_master_slave)
        row.addWidget(self.ms_switch)
        self.slave_label = QtWidgets.QLabel('Slave Address'); row.addWidget(self.slave_label)
        self.slaveAddressText = QtWidgets.QLineEdit(); self.slaveAddressText.setEnabled(False)
        self.slaveAddressText.textChanged.connect(self.on_slave_text)
        row.addWidget(self.slaveAddressText)
        self.zero_all_button = QtWidgets.QPushButton('Zero All')
        self.zero_all_button.clicked.connect(self.zero_all)
        row.addWidget(self.zero_all_button); row.addStretch(1)
        lay.addWidget(box); lay.addLayout(row); lay.addStretch(1)
        self.on_master_slave(False)
        self.set_networks(None)

    def set_networks(self, networks):
        """onAmpConnected FUN_1400ccaf0 (B9(d)): the device's network list, up to 4
        (name, type). MOST25/MOST50 (type 6) or MOST150 (7) -> net A; A2B (3) -> net B;
        none / disconnected -> switch off, net A."""
        self.network, on = 0, False
        for name, typ in list(networks or [])[:4]:
            if (typ == 6 and name in ('MOST25', 'MOST50')) or (typ == 7 and name == 'MOST150'):
                self.network, on = 0, True
                break
            if typ == 3 and name == 'A2B':
                self.network, on = 1, True
                break
        if not on and self.ms_switch.isChecked():
            self.ms_switch.setChecked(False)
        self.ms_switch.setEnabled(on)

    def on_master_slave(self, slave: bool):
        """FUN_1400ccf70: Master pre-loads the sliders from the model; Slave zeroes them."""
        self.mode = 2 if slave else 1
        self.slaveAddressText.setEnabled(slave)
        self.slave_label.setStyleSheet('' if slave else 'color:gray;')
        root = getattr(self.engine, 'root', None)
        hu = getattr(root, 'headunit', None)
        for label, _o, field, *_r in LEVEL_SLIDERS:
            s = self.sliders[label]
            s.blockSignals(True)
            if slave:
                s.setValue(0)
            elif hu is not None and field in getattr(hu, 'FIELDS', ()):
                v = hu.values[field]
                s.setValue(v - 256 if s.minimum() < 0 and v > 127 else v)
            s.blockSignals(False)

    def on_slave_text(self, text):
        try:
            v = int(text.replace('0x', '').replace('0X', ''), 16)
            ok = 0 <= v <= 0xFFFF
        except ValueError:
            ok = False
        self.slaveAddressText.setStyleSheet('' if ok or not text else 'QWidget {color:red;}')
        if ok:
            self.slave = v

    def zero_all(self):
        for label in ('Bass (dB)', 'Mid (dB)', 'Treble (dB)', 'Balance', 'Fade'):
            self.sliders[label].setValue(0)

    def on_slider(self, n: int, value: int):
        label, _o, field, _lo, _hi, fa, fb = LEVEL_SLIDERS[n]
        e = self.engine
        if e is None:
            return None
        if self.mode == 1:
            hu = getattr(getattr(e, 'root', None), 'headunit', None)
            if field is not None and hu is not None and field in hu.FIELDS:
                if hu.set(field, value):
                    e.push('headunit')
            return None
        frame = None
        if self.network == 0 and fa is not None:
            frame = level_net_a(fa, value, volume=(field == 'volume'))
        elif self.network == 1 and fb is not None:
            frame = level_net_b(self.slave, fb, value)
        if frame is not None and getattr(e, 'connected', False):
            e.bus_request_async(frame[0], frame[1], lambda rep: None)
        return frame


def Qt_center():
    return QtCore.Qt.AlignHCenter


class MasternodeScreen(QtWidgets.QWidget):
    title = TITLE

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.setObjectName('MasternodeScreen')
        self.setStyleSheet(QSS)
        outer = QtWidgets.QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        menu = QtWidgets.QWidget(); menu.setObjectName('menuWidget')
        ml = QtWidgets.QVBoxLayout(menu); ml.setSpacing(0); ml.setContentsMargins(0, 0, 0, 0)
        ml.addSpacing(15)
        self.stack = QtWidgets.QStackedWidget()
        self.pages = [ControlsPage(engine), LoggingPage(engine), LevelsPage(engine)]
        self.buttons = []
        for i, (text, page) in enumerate(zip(NAV, self.pages)):
            b = QtWidgets.QPushButton(text); b.setCheckable(True); b.setMinimumWidth(150)
            b.clicked.connect(lambda _c=False, n=i: self.select(n))
            ml.addWidget(b); self.buttons.append(b)
            self.stack.addWidget(page)
        ml.addStretch(1)
        right = QtWidgets.QVBoxLayout(); right.setSpacing(5); right.setContentsMargins(20, 10, 10, 10)
        right.addWidget(self.stack)
        outer.addWidget(menu); outer.addLayout(right, 1)
        self.select(0)

    def select(self, n):
        for b in self.buttons:
            b.setChecked(False)
        self.stack.setCurrentIndex(n)
        self.buttons[n].setChecked(True)
