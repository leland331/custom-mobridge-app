"""Official window chrome: menu bar, logo header with Preset + tool buttons,
tab bar with the Connected button, brushed stage, plus our additive extra tabs
(to the right of the official five) and a live-vs-gated status strip."""
from __future__ import annotations
import os
import sys

from ..qt import QtCore, QtGui, QtWidgets, Qt
from . import skin as S
from . import data
from ... import uirules as R
from .canvas import Canvas, Item
from .screens import OFFICIAL_SCREENS, DeviceScreen, ScriptingScreen

CX, CY, D = S.CX, S.CY, S.D
CLIENT_W, CLIENT_H = 1300, 800 - S.MENU_H   # official area under the menu bar (logical px)
STATUS_H = 0                       # the official window has no status strip

# official tab boxes (reference px, frame coords): x0, x1
OFFICIAL_TAB_X = [(36, 261), (270, 417), (426, 573), (582, 729), (738, 885)]
TAB_Y0, TAB_Y1 = 174.0, 226.0
TAB_GAP = 9.0

# #menuWidget (tab bar) and its checked button, from mobridge_dsp_style.qss
_BAR = [(0.0, '#A3A3A3'), (0.23, '#858585'), (0.63, '#858585'), (1.0, '#A3A3A3')]
_SEL = [(0.0, '#a6a6a6'), (0.23, '#6b6b6b'), (0.63, '#6b6b6b'), (1.0, '#a6a6a6')]
_HOT = [(0.0, '#e45d5d'), (0.23, '#D12323'), (0.63, '#D12323'), (1.0, '#e45d5d')]


def _grad(r, stops, span=None):
    g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
    for t, c in stops:
        g.setColorAt(t, QtGui.QColor(c))
    return QtGui.QBrush(g)


UNDERLAY_Y = 0.0   # #centralWidget starts under the menu bar

MENU_QSS = """
QMenuBar { background: qlineargradient(spread:pad, x1:0 y1:0, x2:0 y2:1, stop:0.0 #A3A3A3,
    stop:0.23 #858585, stop:0.63 #858585, stop:1.0 #A3A3A3);
    color: white; font-family: "Droid Sans"; font-size: 12px; padding: 0px 0px 0px 10px; min-height: 20px; }
QMenuBar::item { padding: 0px 5px; background: transparent; }
QMenuBar { min-height: 23px; max-height: 23px; spacing: 0px; }  /* 35 px at 150 % in the capture */
QMenuBar::item:selected { background: #a8a8a8; }
QMenuBar::item:pressed { background: #343434; }
QMenu { background: qlineargradient(spread:pad, x1:0 y1:0, x2:0 y2:1, stop:0.0 #8a8a8a, stop:1.0 #4e4e4e);
    color: white; border: 1px solid #343434; font-family: "Droid Sans"; font-size: 12px; }
QMenu::separator { height: 1px; background: gray; }
QMenu::item { background-color: transparent; padding: 4px 28px 4px 24px; }
QMenu::item:disabled { color: lightgray; }
QMenu::item:selected { background-color: #343434; }
QToolTip { background: qlineargradient(spread:pad, x1:0 y1:0, x2:0 y2:1, stop:0.0 #8a8a8a, stop:1.0 #4e4e4e);
    color: white; border: 1px solid #343434; padding: 5px; font-family: "Droid Sans"; font-size: 12px; }
"""


class Tab(Item):
    def __init__(self, r, label, index, extra=False):
        super().__init__(r)
        self.label, self.index, self.extra = label, index, extra
        self.selected = False
        self.owner = None

    @property
    def hoverable(self):
        return True

    def paint(self, p):
        if self.pressed and not self.extra:
            p.fillRect(self.r, QtGui.QColor('#d12323'))   # QSS QPushButton:pressed
        elif self.selected:
            p.fillRect(self.r, _grad(self.r, _SEL))
        elif self.hover:
            p.fillRect(self.r, QtGui.QColor(255, 255, 255, 40))
        # added ("extra") tabs render identically to the official five: same Droid
        # Sans, same size 13 and near-white colour, so the tab bar reads as one row.
        S.text(p, self.r.center().x(), CY(200), self.label, 13,
               color=QtGui.QColor(253, 253, 253), align='center', shadow=False)

    def click(self, e):
        self.owner.select(self.index)
        return True


class ConnectedButton(Item):
    def __init__(self):
        super().__init__(S.rect(1745, 178, 1925, 222, client=True),
                         tooltip='Device Tab (Ctrl+T, Ctrl+D')   # sic: closing paren missing in the exe
        self.owner = None
        self.active = False
        self.connected = False   # reflects the real link; stays False until a handshake
        self.state = 0           # connection state (uirules.CONNECT_STATES)
        self.product = None

    @property
    def hoverable(self):
        return True

    def paint(self, p):
        if self.active:
            p.fillRect(self.r, _grad(self.r, _SEL, 44))
        elif self.hover:
            p.fillRect(self.r, QtGui.QColor(255, 255, 255, 20))
        # FUN_1400b2d00: text by connection state (trailing spaces as in the exe;
        # the icon sits right of the text). Disabled look when not connected.
        label = R.connect_button_text(self.state)
        enabled = self.connected and self.state not in (5, 16)
        col = QtGui.QColor(253, 253, 253) if enabled else QtGui.QColor(150, 150, 150)
        S.text(p, CX(1857), CY(200), label.rstrip(), 13, color=col, align='right', shadow=False)
        icon = R.product_image(self.product) if enabled else None
        if icon:
            pm = S.pix(icon)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            p.drawPixmap(S.rect(1867, 184, 1913, 216, client=True), pm, QtCore.QRectF(pm.rect()))

    def click(self, e):
        if self.connected and self.state not in (5, 16):
            self.owner.select('device')
        return True


class HeaderButton(Item):
    def __init__(self, x0, icon, tooltip, action=None):
        super().__init__(S.rect(x0, 113, x0 + 41, 155, client=True), tooltip)
        self.icon = icon
        self.action = action

    @property
    def hoverable(self):
        return True

    def click(self, e):
        if self.action is not None:
            self.action()
        return True

    def paint(self, p):
        r = self.r
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(110, 110, 112))
        p.drawRoundedRect(r, D(3), D(3))
        if self.hover or self.pressed:
            stops = [(0, (202, 202, 204)), (0.5, (178, 178, 180)), (1, (150, 150, 152))]
        else:
            stops = [(0, (176, 176, 178)), (0.5, (150, 150, 152)), (1, (124, 124, 126))]
        p.setBrush(S.vgrad(r.adjusted(D(1), D(1), -D(1), -D(1)), stops))
        p.drawRoundedRect(r.adjusted(D(1), D(1), -D(1), -D(1)), D(3), D(3))
        pm = S.pix(self.icon)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        c = r.center()
        p.drawPixmap(QtCore.QRectF(c.x() - D(13), c.y() - D(13), D(26), D(26)), pm, QtCore.QRectF(pm.rect()))


class LogoButton(Item):
    """The header logo: opens https://mobridge.us (tooltip is the URL)."""

    def __init__(self, open_url):
        super().__init__(S.rect(40, 103, 200, 161, client=True), tooltip=R.HELP_URLS['logo'])
        self.open_url = open_url

    @property
    def hoverable(self):
        return True

    def click(self, e):
        self.open_url(R.HELP_URLS['logo'])
        return True

    def paint(self, p):
        pass                     # drawn by Chrome.paint_static


class PresetCombo(Item):
    def __init__(self):
        super().__init__(S.rect(1557, 113, 1786, 152, client=True),
                         tooltip='Select Preset')
        self.value = data.PRODUCT['preset']
        self.items = list(R.PRESET_ITEMS)
        self.current = self.value
        self.on_select = None    # FUN_1400ed320: settings dirty bit 4 -> 0x0716 sub 1

    def select(self, text):
        if text not in self.items or text == self.current:
            return False
        self.value = self.current = text
        if self.on_select is not None:
            self.on_select(self.items.index(text))
        return True

    def show_preset(self, index: int) -> None:
        if 0 <= index < len(self.items):
            self.value = self.current = self.items[index]

    @property
    def hoverable(self):
        return True

    def click(self, e):
        if self.canvas is not None:
            self.canvas.open_popup(self)
        return True

    def paint(self, p):
        r = self.r
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(112, 112, 114))
        p.drawRoundedRect(r, D(3), D(3))
        p.setBrush(S.vgrad(r.adjusted(D(1), D(1), -D(1), -D(1)),
                           [(0, (158, 158, 160)), (0.5, (139, 139, 141)), (1, (124, 124, 126))]))
        p.drawRoundedRect(r.adjusted(D(1), D(1), -D(1), -D(1)), D(3), D(3))
        S.text(p, CX(1574), CY(132), self.value, 13, color=QtGui.QColor(250, 250, 250), shadow=False)
        pen = QtGui.QPen(QtGui.QColor(245, 245, 245), D(2)); p.setPen(pen)
        cx, cy = CX(1767), CY(131)
        p.drawPolyline([QtCore.QPointF(cx - D(9), cy - D(4.5)), QtCore.QPointF(cx, cy + D(4.5)),
                        QtCore.QPointF(cx + D(9), cy - D(4.5))])


class Chrome(Canvas):
    """Window client: gradient background, logo header, tab bar and the stage frame."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.preset = PresetCombo()
        self.preset.on_select = win.select_preset
        self.add(self.preset)
        self.add(LogoButton(win._open_url))
        self.add(HeaderButton(1796, 'icon_fullscreen_16.png', 'Fullscreen (F11)', win.toggle_fullscreen))
        self.add(HeaderButton(1847, 'icon_usb_16.png', 'Select USB device', win.open_connection))
        self.add(HeaderButton(1898, 'icon_cmd_16.png', 'Opens device debug window (Ctrl+D)', win.open_connection))
        self.tabs = []
        self.connected = self.add(ConnectedButton())
        self.connected.owner = win

    def add_tab(self, label, index, extra):
        if not extra:
            x0, x1 = OFFICIAL_TAB_X[4 if index == 'masternode' else index]
        else:
            prev = self.tabs[-1].r
            x0 = (prev.right() * S.REF + S.CLIENT_ORIGIN[0]) + TAB_GAP + (14 if index == 5 else 0)
            w = QtGui.QFontMetricsF(S.font(13)).horizontalAdvance(label) * S.REF + 28
            x1 = x0 + max(w, 120)
        t = self.add(Tab(S.rect(x0, TAB_Y0, x1, TAB_Y1, client=True), label, index, extra))
        t.owner = self.win
        self.tabs.append(t)
        return t

    def paint_static(self, p):
        w, h = self.width(), self.height()
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        # #centralWidget { background-image: url(:/images/underlay.png) } - unscaled, tiled
        p.drawTiledPixmap(QtCore.QRectF(0, UNDERLAY_Y, w, h), S.pix('window_gradient.png'))
        logo = S.pix('mobridge_logo.png')
        p.drawPixmap(S.rect(40, 103, 200, 161, client=True), logo, QtCore.QRectF(logo.rect()))
        # tab bar (rounded top corners) with a dark line above it
        bar = S.rect(23, TAB_Y0, 1940, TAB_Y1, client=True)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(74, 74, 76))
        p.drawRoundedRect(bar.adjusted(0, -D(2), 0, 0), 10, 10)
        path = QtGui.QPainterPath(); path.addRoundedRect(bar.adjusted(0, 0, 0, 12), 10, 10)
        p.save(); p.setClipRect(bar); p.fillPath(path, _grad(bar, _BAR)); p.restore()
        # stage: brushed texture, rounded bottom corners
        st = S.rect(23, 226, 1940, 1236, client=True)
        path = QtGui.QPainterPath(); path.addRoundedRect(st.adjusted(0, -12, 0, 0), 10, 10)
        p.save(); p.setClipRect(st); p.setClipPath(path, Qt.IntersectClip)
        # #mainWidget { background-image: url(:/images/background.png) } - unscaled, from top-left
        p.drawTiledPixmap(st, S.pix('stage_texture.png'))
        p.restore()


class OfficialMainWindow(QtWidgets.QMainWindow):
    """The official window. extra_tabs: list of (label, QWidget factory) shown right of
    the five official tabs (our additions)."""

    def __init__(self, extra_tabs=None, device_status='Not connected',
                 engine=None, root=None):
        super().__init__()
        S.load_fonts()
        self.engine = engine
        # mobridge.model.DspModel (tune being edited). With an engine the window
        # follows engine.root (replaced by the device's tune on connect).
        self._root = root
        self._auto_answer = None         # tests: canned QMessageBox answer instead of exec()
        self.last_message = None
        self.device_status = device_status
        self.setWindowTitle(R.WINDOW_TITLE)
        self.setWindowIcon(QtGui.QIcon(S.asset_path('mobridge_icon.png')))
        self.setStyleSheet(MENU_QSS)
        self._build_menu()
        self.chrome = Chrome(self)
        self.setCentralWidget(self.chrome)
        self.stack = QtWidgets.QStackedWidget(self.chrome)
        self.stack.setGeometry(S.irect(S.rect(23, 226, 1940, 1236, client=True)))
        self.stack.setStyleSheet('QStackedWidget { background: transparent; }')
        self.pages = {}
        self.tab_index = {}          # tab label (upper) -> page index, for menu/button navigation
        for i, cls in enumerate(OFFICIAL_SCREENS):
            # Scripting lives in Tools ▸ Scripting (project call) rather than the
            # tab bar; its page still exists and is shown when the menu item fires.
            self._add_page(i, cls.title, cls(engine=engine), extra=False, tab=(cls is not ScriptingScreen))
        for j, (label, factory) in enumerate(extra_tabs or []):
            self._add_page(5 + j, label.upper(), factory(), extra=True)
        from .masternode import MasternodeScreen, TOOLTIP as _MN_TIP
        self.masternode = MasternodeScreen(engine=engine)
        self.masternode.setGeometry(0, 0, self.stack.width(), self.stack.height())
        self._add_page('masternode', MasternodeScreen.title, self.masternode, extra=False)
        mn_tab = self.chrome.tabs[-1]
        mn_tab.tooltip = _MN_TIP
        mn = QtGui.QShortcut(QtGui.QKeySequence('Ctrl+T, Ctrl+M'), self)
        mn.activated.connect(lambda: self.select('masternode') if mn_tab.visible else None)
        self.pages['device'] = self.stack.addWidget(DeviceScreen(engine=engine))
        # The official app is a FIXED-size window: its skin is absolute-positioned
        # and the QSS draws background.png unscaled, anchored top-left (so it is not
        # designed to resize). Match that: lock the window to the official client
        # area + our status strip. A resizable window left the content pinned
        # top-left with dead space (2026-10-22) — fixing the size is the
        # faithful behaviour, not scaling the canvas.
        self.setFixedSize(CLIENT_W, CLIENT_H + STATUS_H + S.MENU_H)
        self.select(0)
        # Reflect the real link state in the chrome's "Connected" indicator.
        if engine is not None:
            self.chrome.connected.connected = engine.connected
            engine.mode_changed.connect(self._on_engine_mode)
            engine.state_changed.connect(self._on_engine_state)
            engine.error.connect(self._on_engine_error)
            engine.model_changed.connect(self._sync_preset_ui)
        self.apply_menu_state(bool(engine and engine.connected))

    @property
    def root(self):
        if self.engine is not None and getattr(self.engine, 'root', None) is not None:
            return self.engine.root
        return self._root

    @root.setter
    def root(self, value):
        self._root = value

    def _push(self, what):
        """Flush the model's pending bits to the device (menu actions edit the model
        like the screens do)."""
        if self.engine is not None:
            self.engine.push(what)
        self.chrome.update()

    def _on_engine_error(self, text):
        if text == R.FACTORY_RESET_FAILED and getattr(self, '_auto_answer', None) is None:
            QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, R.APP_NAME, text,
                                  QtWidgets.QMessageBox.Ok, self).exec()

    def _on_engine_state(self, state: int):
        if (state == 5) != (getattr(self, '_shown_state', None) == 5) and 'device' in self.pages:
            dev = self.stack.widget(self.pages['device'])       # FUN_14004d530: the button text
            if hasattr(dev, 'rebuild'):
                dev.rebuild()
        self._shown_state = state
        self.chrome.connected.state = state
        self.chrome.connected.product = getattr(self.engine.root, 'product', None)
        self.chrome.update()

    def _on_engine_mode(self, *_):
        self.chrome.connected.connected = bool(self.engine and self.engine.connected)
        self.apply_menu_state(self.chrome.connected.connected)
        if self.engine is not None and not self.engine.connected:
            self.device_status = 'Not connected'
        self.chrome.update()

    def _add_page(self, index, label, widget, extra, tab=True):
        if extra:
            # our screens use their own theme; give them a dark backdrop on the stage
            widget.setAutoFillBackground(False)
        self.pages[index] = self.stack.addWidget(widget)
        self.tab_index[label.upper()] = index
        if tab:
            self.chrome.add_tab(label, index, extra)

    # --- header button / menu navigation --------------------------------
    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()
        a = self.actions.get('Fullscreen') if hasattr(self, 'actions') else None
        if a is not None:
            a.blockSignals(True); a.setChecked(self.isFullScreen()); a.blockSignals(False)

    def open_connection(self):
        """Jump to the Connection screen (the Select USB Device / Debug Device buttons)."""
        self.select(self.tab_index.get('CONNECTION', 5))

    def import_script_module(self, path=None):
        """actionImportScriptModuleArchive: inflate a .sma into the script folder."""
        from ... import scripting
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, scripting.SMA_FILTER.split('(')[0], '',
                                                            scripting.SMA_FILTER)
            if not path:
                return False
        dest = os.path.join(ScriptingScreen.dirs()[0], os.path.splitext(os.path.basename(path))[0])
        if scripting.import_sma(path, dest):
            return True
        if self._auto_answer is None:
            QtWidgets.QMessageBox.critical(self, scripting.SMA_FAILED_TITLE, scripting.SMA_FAILED)
        return False

    def _fw_ask(self, icon, text, buttons):
        if self._auto_answer is not None:
            return self._auto_answer
        Q = QtWidgets.QMessageBox
        m = Q(icon, R.FW_CONFIRM['title'], text, buttons, self)
        m.setTextFormat(Qt.RichText)
        return m.exec()

    def firmware_update(self, path=None, state=None):
        """FUN_1400a2870 -> FUN_1400ab740 -> FUN_14009dd70: pick, validate, confirm,
        back up, then hand the file to the engine's FlashFile job."""
        from ... import flashflow as F, products, rax
        Q = QtWidgets.QMessageBox
        eng = self.engine
        if eng is None or not eng.connected:
            return False
        state = eng.conn_state if state is None else state
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Firmware Update File', '',
                                                            'Firmware Update (*.fif)')
            if not path:
                return False
        pid = getattr(self.root, 'product', None) or getattr(eng, 'product_id', None) or 0
        if state == R.STATE_RECOVERY:          # Recovery reads no model: the unit's own identify
            pid = getattr(eng, 'product_id', None) or 0
        dev_ver = F._ver3(getattr(eng, 'app_version', '') or '0.0.0')
        plan = F.validate(path, pid, dev_ver, state)
        if plan.code == F.MIN_PREV:
            if self._fw_ask(Q.Question, F.message(plan), Q.Yes | Q.No) != Q.Yes:
                return False
            app_data = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppLocalDataLocation)
            app_dir = QtCore.QCoreApplication.applicationDirPath()
            first = F.find_min_prev([os.path.join(app_data, 'firmware'),
                                     os.path.join(app_dir, '..', 'firmware')], plan.min_prev)
            if first is None:
                return False
            return self.firmware_update(first, state)
        if plan.code in (F.INVALID, F.INCOMPATIBLE_DEVICE, F.INCOMPATIBLE_APP):
            self._fw_ask(Q.Warning, F.message(plan), Q.Ok)
            return False
        if plan.code == F.NVM_FIXED:
            if self._fw_ask(Q.Warning, F.message(plan), Q.Ok | Q.Cancel) != Q.Ok:
                return False
        if plan.code == F.OLDER:
            if self._fw_ask(Q.Warning, R.FW_VALIDATE[1] + ' Do you want to proceed?', Q.Yes | Q.No) != Q.Yes:
                return False
        if plan.guard:                       # our one addition: fif.bootblock_problem (per family)
            self._fw_ask(Q.Warning, R.FW_VALIDATE[6], Q.Ok)
            return False
        if self._fw_ask(Q.Warning, F.confirm_text(plan, dev_ver, state), Q.Ok | Q.Cancel) != Q.Ok:
            return False
        folder = os.path.join(QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.AppLocalDataLocation), 'backup')
        os.makedirs(folder, exist_ok=True)
        name = products.name(pid) if pid in products.BY_ID else 'device'
        rax.save(self.root, F.backup_path(folder, pid, name))
        return eng.flash_file(plan)

    def open_scripting(self):
        self.select(self.tab_index.get('SCRIPTING', 4))

    def select(self, index):
        self.stack.setCurrentIndex(self.pages[index])
        for t in self.chrome.tabs:
            t.selected = t.index == index
        self.chrome.connected.active = index == 'device'
        self.chrome.update()

    # --- menus: built from the retranslateUi/setupUi tables in uirules ------
    # Each MENU_* row is (text, shortcut, checkable, start-enabled, start-visible),
    # straight from the exe (re/analysis/UI_MAINWINDOW.md §A/§B). the reference two
    # standing exceptions are marked [CUSTOM]: Scripting lives in Tools, and the
    # auto-update toggles start OFF so an update never overwrites LB-PAC01.
    def _menu_rows(self, menu, rows, handlers=None):
        handlers = handlers or {}
        for row in rows:
            if row is None:
                menu.addSeparator(); continue
            text, key, checkable, enabled, visible = row
            a = menu.addAction(text)
            if key:
                a.setShortcut(QtGui.QKeySequence(key))
            a.setCheckable(checkable)
            a.setEnabled(enabled)
            a.setVisible(visible)
            if text in handlers:
                a.triggered.connect(handlers[text])
            self.actions[text] = a
        return menu

    def _build_menu(self):
        self.actions = {}
        mb = self.menuBar()
        self._menu_rows(mb.addMenu('File'), R.MENU_FILE, {
            'New Tune': self.file_new, 'Save Tune': self.file_save, 'Save Tune As...': self.file_save_as,
            'Open Tune...': self.file_open, 'Fullscreen': self.toggle_fullscreen, 'Exit': self.close,
            'Import Script Module...': self.import_script_module})
        self.actions['Import Script Module...'].setVisible(True)   # scripting is on in this app
        o = mb.addMenu('Options')
        o.setToolTipsVisible(True)
        self._menu_rows(o, R.MENU_OPTIONS[:1])
        fu = self._menu_rows(o.addMenu('Firmware Update'), R.MENU_OPTIONS_UPDATES)
        for t, _k, _c, _e, _v in R.MENU_OPTIONS_UPDATES:
            a = self.actions[t]
            a.setChecked(False)   # [CUSTOM] official default is on; off protects the custom firmware
        self._menu_rows(o, R.MENU_OPTIONS[1:])
        for name, tip in R.OPTIONS_TOOLTIPS.items():
            self.actions[name].setToolTip(tip)
        # AppSettings bool ids 7 / 9 (NVM_RESET_SETTINGS item 11), kept between runs
        # (our own settings scope, so the official app's settings are never touched)
        st = QtCore.QSettings('CustomMobridge', 'CustomMobridge')
        for name, key, default in (('Add Filter Automatically', 'AddFilterToSpeakersAuto', True),
                                   ('Ignore Load Warnings', 'IgnoreLoadWarnings', False)):
            a = self.actions[name]
            a.setChecked(str(st.value(key, default)).lower() in ('true', '1'))
            a.toggled.connect(lambda on, k=key: QtCore.QSettings('CustomMobridge', 'CustomMobridge').setValue(k, bool(on)))
        hu = self._menu_rows(o.addMenu('Head Unit'), R.MENU_OPTIONS_HEADUNIT)
        self.actions['Bass Control Sets Sub Gain Offset'].setToolTip(R.HEADUNIT_TOOLTIPS['bass'])
        fx = self._menu_rows(o.addMenu('Effects'), R.MENU_OPTIONS_EFFECTS)
        self.actions['Rear Fill'].setToolTip(R.HEADUNIT_TOOLTIPS['rearfill'])
        # FUN_140033d30 / FUN_140032320: settings keys 4 / 5 -> DspParamWrite(0xc)
        for name, key in (('Bass Control Sets Sub Gain Offset', 4), ('Rear Fill', 5)):
            self.actions[name].toggled.connect(lambda on, k=key: self._set_effect(k, on))
        self.menu_options, self.menu_headunit, self.menu_effects, self.menu_updates = o, hu, fx, fu
        # Head Unit / Effects sub-menus appear only when the device grants them
        # (fw>=210 + master-EQ capability); Options itself is hidden at start.
        hu.menuAction().setVisible(False)
        fx.menuAction().setVisible(False)
        o.menuAction().setVisible(False)
        t = self._menu_rows(mb.addMenu('Tools'), R.MENU_TOOLS, {
            'Select USB Device...': self.open_connection, 'Debug Device...': self.open_connection,
            'Mute All': self.mute_all, 'Unmap all': self.unmap_all,
            'Firmware Update...': lambda: self.firmware_update()})
        # [CUSTOM] our Connection screen stands in for the mobridgedebug-only items.
        for n, icon in (('Select USB Device...', 'icon_usb_16.png'), ('Debug Device...', 'icon_cmd_16.png')):
            self.actions[n].setIcon(QtGui.QIcon(S.asset_path(icon)))
            self.actions[n].setVisible(True)
        # dead items in the exe (no slot connected) stay dead here too
        for n in ('Configure DRC...', 'Configure Time Alignment...'):
            self.actions[n].setVisible(False)
        sc = t.addAction('Scripting...')               # [CUSTOM] moved here from the tab bar
        sc.triggered.connect(self.open_scripting)
        self.actions['Scripting...'] = sc
        self._menu_rows(mb.addMenu('Help'), R.MENU_HELP, {
            'UI User Manual': lambda: self._open_url('https://' + R.HELP_URLS['ui_manual']),
            'About': self.about})
        for n, icon in R.MENU_ICONS.items():
            self.actions[n].setIcon(QtGui.QIcon(S.asset_path(icon)))
        self.actions['Device User Manual'].setToolTip(R.OFFLINE_HELP_TOOLTIP)
        self.actions['Device Install Guide'].setToolTip(R.OFFLINE_HELP_TOOLTIP)
        # tab shortcuts (retranslateUi: Ctrl+C/E/T/L/D for the visible tabs)
        self._tab_shortcuts = []
        for key, idx in (('config', 0), ('channel_eq', 1), ('time_align', 2), ('limiter', 3), ('device', 'device')):
            s = QtGui.QShortcut(QtGui.QKeySequence(R.TAB_SHORTCUTS[key]), self)
            s.activated.connect(lambda i=idx: self.select(i))
            self._tab_shortcuts.append(s)

    def apply_menu_state(self, connected: bool, caps: int | None = None, fw: int | None = None, state: int = 4):
        """FUN_1400365c0 + FUN_140037060: connect-dependent enable/visible flags,
        from the connected product's flag word (exe product table)."""
        from ... import products
        r = self.root
        if r is None:                  # no engine, no tune: the screens show the offline preview
            from . import data         # (screens.py: data.demo_model), so the menus follow it too
            r = data.demo_model()
        if caps is None:
            pid = getattr(r, 'product', None)
            caps = products.flags(pid)                     # 0 for a product not in the table
        if fw is None:
            fw = getattr(r, 'fw', 0) or 210
        ms = R.menu_state(connected, caps, fw, state)
        pid = getattr(r, 'product', None)
        n = products.channel_count(pid) if pid in products.BY_ID else (getattr(r, 'channel_count', 0) or 0)
        tv = R.tab_visibility(connected, caps, fw, n, True)
        keys = {0: 'config', 1: 'channel_eq', 2: 'time_align', 3: 'limiter', 'masternode': 'masternode'}
        for t in self.chrome.tabs:
            if not t.extra and t.index in keys:
                t.visible = tv[keys[t.index]]
                if t.index == 0:
                    t.label = tv['config_text'].replace('&&', '&')
        self.actions['Mute All'].setEnabled(ms['mute_all'])
        self.actions['Unmap all'].setEnabled(ms['unmap_all'])
        from ... import parity
        self.actions['Firmware Update...'].setEnabled(ms['firmware_update'] and 0x0201 not in parity.LOCKED_COMMANDS)
        self.actions['Add Filter Automatically'].setVisible(ms['add_filter_auto_visible'])
        self.actions['Add Filter Automatically'].setEnabled(connected)
        self.menu_headunit.menuAction().setVisible(ms['headunit_menu_visible'])
        self.menu_effects.menuAction().setVisible(ms['headunit_menu_visible'])
        for name in ('Bass Control Sets Sub Gain Offset', 'Rear Fill'):
            self.actions[name].setVisible(ms['headunit_menu_visible'])
        for n in ('Device User Manual', 'Device Install Guide'):
            self.actions[n].setVisible(ms['help_device_links_visible'])
            self.actions[n].setEnabled(ms['help_device_links_visible'])
        # Options appears on connect (the exe shows it on first sync)
        self.menu_options.menuAction().setVisible(connected)
        self.chrome.preset.enabled = ms['preset_combo']
        self.chrome.update()

    # --- File menu ---------------------------------------------------------
    def _dirty(self) -> bool:
        if self.root is not None and self.root.is_modified():
            return True
        return bool(self.engine is not None and self.engine.dirty)

    def _ask_dirty(self):
        """Save | Discard | Cancel. Returns 'save' | 'discard' | 'cancel'."""
        d = R.dirty_prompt(bool(self.engine and self.engine.connected))
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, d['title'], d['text'],
                                    QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
                                    | QtWidgets.QMessageBox.Cancel, self)
        box.setDefaultButton(QtWidgets.QMessageBox.Save)
        if self._auto_answer is not None:
            r = self._auto_answer
        else:
            r = box.exec()
        return {QtWidgets.QMessageBox.Save: 'save', QtWidgets.QMessageBox.Discard: 'discard'}.get(r, 'cancel')

    def _confirm_discard(self) -> bool:
        if not self._dirty():
            return True
        r = self._ask_dirty()
        if r == 'save':
            return self.file_save()
        return r == 'discard'

    def closeEvent(self, e):
        # FUN_14002ff90: device busy (states 7, 8, 11) -> Yes|Cancel, then the dirty prompt
        st = getattr(self.engine, 'conn_state', 0) if self.engine is not None else 0
        if st in (7, 8, 11):
            Q = QtWidgets.QMessageBox
            box = Q(Q.Question, R.APP_NAME, R.EXIT_DURING_PROCESS, Q.Yes | Q.Cancel, self)
            r = self._auto_answer if self._auto_answer is not None else box.exec()
            if r == Q.Cancel:
                e.ignore()
                return
        if self._confirm_discard():
            e.accept()
        else:
            e.ignore()

    def file_new(self):
        if not self._confirm_discard():
            return False
        if self.root is not None:
            self.root.new_tune(connected=bool(self.engine and self.engine.connected))
        if self.engine is not None:
            self.engine.discard()
        self._refresh_title()
        return True

    def _rax_dialog(self, save: bool):
        start = self.root.path if (self.root is not None and self.root.path) else ''
        fn = QtWidgets.QFileDialog.getSaveFileName if save else QtWidgets.QFileDialog.getOpenFileName
        path, _ = fn(self, R.FILE_DIALOG['title'], start, R.FILE_DIALOG['filter'])
        if save and path and not path.lower().endswith('.rax'):
            path += '.rax'
        return path

    def file_save(self) -> bool:
        if self.root is None:
            if self.engine is not None:
                p = self._rax_dialog(True)
                if not p:
                    return False
                self.engine.export_backup(p)
                return True
            return False
        if not self.root.path:
            return self.file_save_as()
        return self._save_to(self.root.path)

    def file_save_as(self) -> bool:
        p = self._rax_dialog(True)
        if not p:
            return False
        return self._save_to(p)

    def _save_to(self, path) -> bool:
        from ... import rax
        rax.save(self.root, path)
        self.root.path = path
        self.root.modified = False
        if self.engine is not None:
            self.engine.discard()
        self._refresh_title()
        return True

    def file_open(self) -> bool:
        if self.root is None:
            return False
        if not self._confirm_discard():
            return False
        p = self._rax_dialog(False)
        if not p:
            return False
        return self.load_rax(p)

    def restore_backup(self) -> bool:
        """Device ▸ Restore (FUN_14004dfb0): the Open Tune flow in AppData/backup."""
        if self.root is None or not self._confirm_discard():
            return False
        base = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
        start = os.path.join(base, 'backup')
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, R.FILE_DIALOG['title'], start, R.FILE_DIALOG['filter'])
        return self.load_rax(path) if path else False

    def load_rax(self, path, force=False) -> bool:
        """Open Tune: parse + apply to the local model. Connected: the official app
        warns (LOAD_WARNING, Yes|No|Cancel) and then writes everything to the device;
        here the load only fills the model and the gated sync decides what may go out."""
        from ... import rax
        base = os.path.basename(path)
        if self.engine is not None and self.engine.connected and not self.actions['Ignore Load Warnings'].isChecked():
            box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning, R.APP_NAME,
                                        R.LOAD_WARNING.replace('%1', base),
                                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel, self)
            r = self._auto_answer if self._auto_answer is not None else box.exec()
            if r != QtWidgets.QMessageBox.Yes:
                return False
        try:
            rax.load(self.root, path, force=force)
        except rax.RaxError as err:
            if err.code == 5 and not force:
                box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, R.APP_NAME,
                                            R.LOAD_OTHER_DEVICE.replace('%1', base),
                                            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, self)
                r = self._auto_answer if self._auto_answer is not None else box.exec()
                if r == QtWidgets.QMessageBox.Yes:
                    return self.load_rax(path, force=True)
                return False
            msg = R.LOAD_ERRORS.get(err.code, R.LOAD_ERRORS[1]).replace('%1', base).replace('%2', str(err.code)) \
                .replace('%2', R.APP_NAME)
            self._message(QtWidgets.QMessageBox.Critical, R.APP_NAME, msg + '<br><br>' + R.LOAD_ERROR_SUFFIX)
            return False
        except OSError:
            self._message(QtWidgets.QMessageBox.Critical, R.APP_NAME,
                          R.LOAD_ERRORS[3].replace('%1', base) + '<br><br>' + R.LOAD_ERROR_SUFFIX)
            return False
        self.root.path = path
        # Connected: the official app writes the loaded tune to the device (rax.load
        # marked every bit); offline the model just holds it.
        self._push('open tune')
        self._refresh_title()
        return True

    def select_preset(self, index: int) -> None:
        """Preset combo: the model selects it (dirty bit 4) and the flush sends
        0x0716 sub 1; Mute All then follows the new preset (UI_MAINWINDOW C.3)."""
        if self.root is not None and self.root.select_preset(index):
            self._push('preset')
        self._sync_preset_ui()

    def _set_effect(self, key: int, on: bool) -> None:
        r = self.root
        if r is None or r.option(key) == bool(on):
            return
        r.set_option(key, on)
        self._push('rear fill' if key == 5 else 'bass control')

    def _sync_preset_ui(self, *_):
        r = self.root
        if r is None:
            return
        for name, key in (('Bass Control Sets Sub Gain Offset', 4), ('Rear Fill', 5)):
            a = self.actions.get(name)
            if a is not None:
                a.blockSignals(True); a.setChecked(r.option(key)); a.blockSignals(False)
        self.chrome.preset.show_preset(r.current_preset)
        a = self.actions.get('Mute All')
        if a is not None and a.isCheckable():
            a.blockSignals(True); a.setChecked(r.all_muted()); a.blockSignals(False)
        self.chrome.update()

    # --- Tools / Help --------------------------------------------------------
    def mute_all(self, checked=None):
        if self.root is not None:
            self.root.mute_all(bool(checked))
        self._push('mute all')

    def unmap_all(self):
        if self.root is None:
            return False
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Question, R.APP_NAME, R.UNMAP_ALL_PROMPT,
                                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, self)
        r = self._auto_answer if self._auto_answer is not None else box.exec()
        if r == QtWidgets.QMessageBox.Yes:
            self.root.unmap_all()
            self._push('unmap all')
            return True
        return False

    def about(self):
        a = R.ABOUT
        self._message(QtWidgets.QMessageBox.Information, a['title'],
                      '<b>%s</b><br>Version %s<br>Build %s (%s)<br>Qt %s<br><br>%s'
                      % (R.APP_NAME, a['version'], a['build_sha'], a['build_date'], a['qt'], a['copyright']))

    def _message(self, icon, title, text):
        if self._auto_answer is not None:
            self.last_message = (title, text)
            return
        QtWidgets.QMessageBox(icon, title, text, QtWidgets.QMessageBox.Ok, self).exec()

    @staticmethod
    def _open_url(url):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _refresh_title(self):
        # exe: plain "mObridge DSP - v25.8.0"; the tune name lives on the CONFIG page.
        self.setWindowTitle(R.WINDOW_TITLE)


def default_extra_tabs(eng=None):
    """Our additive screens from mobridge.gui.screens (owned by the app-prototype thread).
    `eng` is the one Engine the whole window shares (official tabs + ours)."""
    try:
        from ..engine import Engine
        from ..screens import (ConnectionScreen, FirmwareScreen, FilesystemScreen, BusToolsScreen,
                               DebugConsoleScreen)
    except Exception:  # pragma: no cover - keeps the skin usable on its own
        return []
    eng = eng or Engine()

    def wrap(cls):
        def make():
            from .. import theme
            w = cls(eng)
            holder = QtWidgets.QWidget()
            holder.setObjectName('extraHolder')
            holder.setStyleSheet('QWidget#extraHolder { background: transparent; }' + theme.QSS)
            lay = QtWidgets.QVBoxLayout(holder); lay.setContentsMargins(18, 14, 18, 14); lay.addWidget(w)
            return holder
        return make
    return [('Connection', wrap(ConnectionScreen)), ('Firmware', wrap(FirmwareScreen)),
            ('Device Files', wrap(FilesystemScreen)), ('Bus Tools', wrap(BusToolsScreen)),
            ('Debug Console', wrap(DebugConsoleScreen))]


def run(argv=None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or sys.argv)
    try:
        from ..engine import Engine
        eng = Engine()
    except Exception:  # pragma: no cover
        eng = None
    w = OfficialMainWindow(default_extra_tabs(eng), engine=eng)
    w.show()
    return app.exec()
