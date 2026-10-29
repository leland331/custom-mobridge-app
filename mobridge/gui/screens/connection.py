from ... import uirules as UR
from ...modes import DeviceMode
from ...transport import DEFAULT_BAUD
from ..qt import QtWidgets, Signal
from .base import Screen


class ConnectionScreen(Screen):
    """UI_SPEC §2: like the official app, the Mobridge is found automatically: USB
    first, then a network bridge (a Raspberry Pi sharing the Mobridge over
    RFC2217, found by mDNS or a quick LAN scan). One button does that search.

    The "Choose manually" rows are only the fallback: pick a USB adapter, or pick /
    type a network bridge address (rfc2217://address:4000) and press Enter. The
    last good bridge is remembered in cfg.ini (`networkport`).

    The status line is the single place that says what is going on; it is drawn
    from the engine's connection state (uirules.connection_status)."""
    _found = Signal(object)                # bridge-finder thread -> UI thread
    title = 'Connection'

    def __init__(self, engine, parent=None, options=None):
        super().__init__(engine, parent)
        if options is None:
            from ..config import Options
            options = getattr(engine, 'options', None) or Options()
        self.options = options
        self._find_cancel = None           # threading.Event while a network search runs
        self._error = ''                   # why the last attempt did not connect
        self._product = None               # connected product's name
        lay = QtWidgets.QVBoxLayout(self)
        self.header(lay, 'Connection', 'Finds your Mobridge automatically.')

        top = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel()
        self.status.setObjectName('modeIndicator')
        self.status.setWordWrap(True)
        self.connect_btn = QtWidgets.QPushButton('Connect')
        self.connect_btn.setDefault(True)
        top.addWidget(self.status, 1); top.addWidget(self.connect_btn)
        lay.addLayout(top)

        form = QtWidgets.QFormLayout()
        self.f_name = QtWidgets.QLineEdit(); self.f_name.setReadOnly(True)
        self.f_ver = QtWidgets.QLineEdit(); self.f_ver.setReadOnly(True)
        self.f_prod = QtWidgets.QLineEdit(); self.f_prod.setReadOnly(True)
        self.f_serial = QtWidgets.QLineEdit(); self.f_serial.setReadOnly(True)
        form.addRow('Name', self.f_name)
        form.addRow('Firmware', self.f_ver)
        form.addRow('Product ID', self.f_prod)
        form.addRow('Serial', self.f_serial)
        box = QtWidgets.QGroupBox('Device'); box.setLayout(form)
        lay.addWidget(box)

        manual = QtWidgets.QFormLayout()
        self.port = QtWidgets.QComboBox()          # not editable: pick, don't type
        self.net_box = QtWidgets.QComboBox(); self.net_box.setEditable(True)
        self.net_port = self.net_box.lineEdit()
        self.net_port.setText(self.options.get('networkport') or '')
        self.net_port.setPlaceholderText('rfc2217://address:4000')
        manual.addRow('USB device', self.port)
        manual.addRow('Network bridge', self.net_box)
        mbox = QtWidgets.QGroupBox('Choose manually'); mbox.setLayout(manual)
        self.manual_btn = QtWidgets.QPushButton('Choose manually')
        self.manual_btn.setCheckable(True); self.manual_btn.setFlat(True)
        self.manual_btn.toggled.connect(mbox.setVisible)
        mbox.setVisible(False)
        self.manual_box = mbox
        lay.addWidget(self.manual_btn); lay.addWidget(mbox)
        lay.addStretch(1)

        self.connect_btn.clicked.connect(self._connect_clicked)
        self.port.activated.connect(self._pick)
        self.net_port.returnPressed.connect(self._connect_network)
        self.net_box.activated.connect(lambda _i: self._connect_network())
        self._found.connect(self._on_found)
        engine.mode_changed.connect(self._on_mode)
        engine.device_info.connect(self._on_info)
        if hasattr(engine, 'state_changed'):
            engine.state_changed.connect(lambda _s: self._refresh())
        if hasattr(engine, 'error'):
            engine.error.connect(self._on_error)
        self._scan_and_connect()

    # --- status line + button, from the engine's state ------------------------
    def _refresh(self):
        e = self.engine
        searching = self._find_cancel is not None
        state = getattr(e, 'conn_state', 0)
        if not e.connected and state in (3, 4, 16):
            state = 14
        self.status.setText(UR.connection_status(
            state, getattr(e, 'port', None), searching=searching,
            reconnecting=bool(getattr(e, 'reconnecting', False)),
            product=self._product, error=self._error))
        busy = e.connected or bool(getattr(e, 'connecting', False)) or bool(getattr(e, 'reconnecting', False))
        self.connect_btn.setText('Stop' if searching else 'Connected' if e.connected else 'Connect')
        self.connect_btn.setEnabled(searching or not busy)

    def _connect_clicked(self):
        if self._find_cancel is not None:
            self._find_cancel.set()                 # stop the network search
        else:
            self._scan_and_connect()

    # --- auto-detect + auto-connect (behind the scenes, like the official app) --
    def _detected_ports(self):
        try:
            from ...transport import list_ports
            return list_ports()          # [(device, desc, is_ftdi)]
        except Exception:
            return []

    def _scan_and_connect(self):
        self._error = ''
        ports = self._detected_ports()
        self.port.clear()
        for dev, desc, is_ftdi in ports:
            self.port.addItem('%s  (%s)%s' % (dev, desc, '  [Mobridge]' if is_ftdi else ''), dev)
        ftdi = [p for p in ports if p[2]]
        if not ftdi:                       # no USB Mobridge: look for a network bridge
            self.port.setCurrentIndex(-1)
            self._start_find()
            return
        self.port.setCurrentIndex([p[0] for p in ports].index(ftdi[0][0]))
        self._connect(ftdi[0][0])

    def _connect(self, port):
        self._error = ''
        self.engine.connect_device(port, DEFAULT_BAUD)
        self._refresh()

    def _pick(self, _index):
        dev = self.port.currentData()
        if dev:
            self._connect(dev)

    def _connect_network(self):
        from ...transport import is_network_port
        i = self.net_box.currentIndex()
        picked = i >= 0 and self.net_box.currentText() == self.net_box.itemText(i)
        url = self.net_box.itemData(i) if picked else self.net_port.text().strip()
        if not is_network_port(url or ''):
            self._error = 'A network bridge address looks like rfc2217://address:4000.'
            self._refresh()
            return
        self._connect(url)

    # --- network bridge discovery (sends nothing to the Mobridge) ---------------
    def _start_find(self):
        import threading
        from ... import netfind
        if self._find_cancel is not None:
            return
        cancel = self._find_cancel = threading.Event()
        self._refresh()

        def run():
            try:
                hits = netfind.find_bridges(cancel=cancel)
            except Exception:
                hits = []
            self._found.emit(hits)
        threading.Thread(target=run, name='bridge-find', daemon=True).start()

    def _on_found(self, hits):
        self._find_cancel = None
        typed = self.net_port.text()
        self.net_box.clear()
        for label, url in hits:
            host = url.split('//', 1)[1]
            self.net_box.addItem(url if label == host.split(':')[0] else '%s  (%s)' % (label, host), url)
        saved = (self.options.get('networkport') or '').strip()
        urls = [u for _l, u in hits]
        pick = saved if saved in urls else urls[0] if len(urls) == 1 else None
        if pick:
            self.net_box.setCurrentIndex(urls.index(pick))
            self._connect_network()
            return
        self.net_box.setCurrentIndex(-1); self.net_port.setText(typed or saved)
        if hits:
            self._error = 'Found %d network bridges. Pick one below.' % len(hits)
            self.manual_btn.setChecked(True)
        else:
            self._error = 'No Mobridge found over USB or on the network.'
        self._refresh()

    # --- engine signals ---------------------------------------------------------
    def _on_error(self, text):
        if not self.engine.connected:
            self._error = text
            self._refresh()

    def _on_mode(self, mode: DeviceMode):
        if mode is DeviceMode.DISCONNECTED:
            self._product = None
            for f in (self.f_name, self.f_ver, self.f_prod, self.f_serial):
                f.clear()
        else:
            self._error = ''
            from ...transport import is_network_port
            port = getattr(self.engine, 'port', None) or ''
            if is_network_port(port) and self.options.get('networkport') != port:
                self.options.set('networkport', port)        # remember the last good bridge
                try:
                    self.options.save()
                except OSError:
                    pass
        self._refresh()

    def _on_info(self, info: dict):
        """engine.device_info: {product, version, info (boot-log strings), serial, ...}."""
        from ... import products
        pid = info.get('product')
        log = info.get('info') or {}
        self._product = products.name(pid) if pid is not None else None
        self.f_name.setText(log.get('Name') or self._product or '')
        self.f_ver.setText(info.get('version') or '')
        self.f_prod.setText('0x%04x' % pid if pid is not None else '')
        self.f_serial.setText(info.get('serial') or '')
        self._refresh()
