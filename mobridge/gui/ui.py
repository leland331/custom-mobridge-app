"""Custom Mobridge main window — tabbed layout matching the official mObridge
DSP v25.8.0 app (UI_SPEC §16): logo header, File/Options/Tools/Help menu, the
five official tabs plus our additive extra tabs, and a live/blocked status bar.
The parity send-gate underneath is unchanged: value controls render disabled."""
from __future__ import annotations

from ..modes import DeviceMode
from . import theme, faithful as F
from .config import Options
from .engine import Engine
from .qt import QtGui, QtWidgets, Qt
from .screens_faithful import (ConfigMasterEqScreen, ChannelEqScreen, TimeAlignScreen,
                               LimiterScreen, ScriptingScreen)
from .screens import (ConnectionScreen, FirmwareScreen, FilesystemScreen,
                      BusToolsScreen, DebugConsoleScreen, ConfigurationScreen,
                      DiagnosticsHealthScreen)

APP_NAME = 'Custom Mobridge'

# Official tabs (always shown, matching the connected app) + our additive extras.
OFFICIAL_TABS = [ConfigMasterEqScreen, ChannelEqScreen, TimeAlignScreen, LimiterScreen, ScriptingScreen]
EXTRA_TABS = [ConnectionScreen, ConfigurationScreen, DiagnosticsHealthScreen, FirmwareScreen,
              FilesystemScreen, BusToolsScreen, DebugConsoleScreen]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, engine: Engine | None = None, options: Options | None = None):
        super().__init__()
        self.engine = engine or Engine(self)
        self.options = options or Options()
        self.setWindowTitle('mObridge DSP  —  Custom Mobridge')
        icon = QtGui.QIcon(F.asset('app_icon.png'))
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1200, 760)

        self._build_menu()

        central = QtWidgets.QWidget(); central.setObjectName('central')
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        outer.addWidget(self._build_header())
        outer.addWidget(self._build_tabstrip())

        self.stack = QtWidgets.QStackedWidget()
        stageWrap = QtWidgets.QVBoxLayout(); stageWrap.setContentsMargins(10, 6, 10, 10)
        holder = QtWidgets.QWidget(); holder.setLayout(stageWrap); stageWrap.addWidget(self.stack)
        outer.addWidget(holder, 1)

        self.screens = []
        for cls in OFFICIAL_TABS:
            self._add_tab(cls, extra=False)
        self._sep_index = len(self.screens)
        for cls in EXTRA_TABS:
            if getattr(cls, 'flag', None) and not self.options.bool(cls.flag):
                continue
            self._add_tab(cls, extra=True)

        self._build_statusbar()
        self.engine.mode_changed.connect(self._on_mode)
        self.engine.error.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self._on_mode(self.engine.mode)
        if self._tab_buttons:
            self._tab_buttons[0].setChecked(True); self.stack.setCurrentIndex(0)

    # --- chrome ----------------------------------------------------------
    def _build_menu(self):
        mb = self.menuBar()
        filem = mb.addMenu('File')
        for t in ['New Tune', 'Save Tune', 'Save Tune As…', 'Open Tune…', None,
                  'Import Script Module…', None, 'Fullscreen', 'Exit']:
            filem.addSeparator() if t is None else filem.addAction(t)
        opt = mb.addMenu('Options')
        a = opt.addAction('Add Filter Automatically'); a.setCheckable(True); a.setChecked(True)
        fu = opt.addMenu('Firmware Update')
        for t in ['Check for Updates Automatically', 'Download Updates Automatically']:
            x = fu.addAction(t); x.setCheckable(True); x.setChecked(False)  # off: never auto-overwrite custom fw
        ilw = opt.addAction('Ignore Load Warnings'); ilw.setCheckable(True)
        opt.addMenu('Head Unit').addAction('Bass Control Sets Sub Gain Offset')
        opt.addMenu('Effects').addAction('Rear Fill')
        tools = mb.addMenu('Tools')
        for t in ['Select USB Device…', 'Debug Device…', 'Mute All', 'Unmap all', 'Firmware Update…']:
            tools.addAction(t)
        helpm = mb.addMenu('Help')
        for t in ['UI User Manual', 'Device User Manual', 'Device Install Guide', None, 'About']:
            helpm.addSeparator() if t is None else helpm.addAction(t)

    def _build_header(self):
        h = QtWidgets.QWidget(); h.setObjectName('header'); h.setFixedHeight(76)
        lay = QtWidgets.QHBoxLayout(h); lay.setContentsMargins(22, 8, 18, 8)
        m = QtWidgets.QLabel('m'); m.setObjectName('wordmarkM')
        rest = QtWidgets.QLabel('Obridge'); rest.setObjectName('wordmarkRest')
        lay.addWidget(m); lay.addWidget(rest); lay.addStretch(1)
        self.preset = QtWidgets.QComboBox(); self.preset.addItems(['Preset 1', 'Preset 2', 'Preset 3', 'Preset 4'])
        self.preset.setFixedWidth(150); lay.addWidget(self.preset)
        for t in ('⛶', '🖧', '>_'):
            b = QtWidgets.QToolButton(); b.setObjectName('hdrBtn'); b.setText(t); lay.addWidget(b)
        return h

    def _build_tabstrip(self):
        strip = QtWidgets.QWidget(); strip.setObjectName('tabstrip'); strip.setFixedHeight(46)
        lay = QtWidgets.QHBoxLayout(strip); lay.setContentsMargins(18, 0, 18, 0); lay.setSpacing(2)
        self.tabgroup = QtWidgets.QButtonGroup(self); self.tabgroup.setExclusive(True)
        self._tab_buttons = []
        self._lay = lay
        self._connected = QtWidgets.QLabel('Connected'); self._connected.setObjectName('connected')
        self._thumb = QtWidgets.QLabel()
        tp = F.pix('device_ford.png')
        if not tp.isNull():
            self._thumb.setPixmap(tp.scaledToHeight(26, Qt.SmoothTransformation))
        return strip

    def _add_tab(self, cls, extra):
        screen = cls(self.engine)
        idx = self.stack.addWidget(screen); self.screens.append(screen)
        btn = QtWidgets.QToolButton(); btn.setObjectName('tabExtra' if extra else 'tab')
        label = getattr(cls, 'title', cls.__name__)
        btn.setText((label if not extra else label.upper()).replace('&','&&')); btn.setCheckable(True)
        btn.clicked.connect(lambda _=False, i=idx: self.stack.setCurrentIndex(i))
        self.tabgroup.addButton(btn)
        if extra and len(self._tab_buttons) and not hasattr(self, '_added_sep'):
            sep = QtWidgets.QLabel('│'); sep.setStyleSheet('color:#444;'); self._lay.addWidget(sep)
            self._lay.addStretch(0); self._added_sep = True
            self._lay.addWidget(self._connected); self._lay.addWidget(self._thumb)
            self._lay.addSpacing(16)
        self._lay.addWidget(btn)
        self._tab_buttons.append(btn)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.mode_label = QtWidgets.QLabel()
        sb.addPermanentWidget(self.mode_label)

    def _on_mode(self, mode: DeviceMode):
        self.mode_label.setText('  Device: %s  ' % mode.name.title())

    # --- save-before-close prompt (file backup) --------------------------
    def _apply_close_choice(self, choice, path=None):
        if choice == 'discard':
            self.engine.discard(); return True
        if choice == 'cancel':
            return False
        if choice == 'save':
            if not path:
                return False
            self.engine.export_backup(path); return True
        return True

    def closeEvent(self, event):  # pragma: no cover - needs a dialog
        if not self.engine.dirty:
            event.accept(); return
        box = QtWidgets.QMessageBox(self); box.setWindowTitle('Save changes?')
        box.setText('You have changes not yet backed up to a file.\n\n'
                    '(Your edits are already saved on the amplifier.) Export a settings backup (.rax)?')
        box.setStandardButtons(QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel)
        ans = box.exec()
        choice = {QtWidgets.QMessageBox.Save: 'save', QtWidgets.QMessageBox.Discard: 'discard',
                  QtWidgets.QMessageBox.Cancel: 'cancel'}.get(ans, 'cancel')
        path = None
        if choice == 'save':
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export settings backup',
                                                            'mobridge_settings.rax', 'Mobridge config (*.rax)')
            if not path:
                event.ignore(); return
        event.accept() if self._apply_close_choice(choice, path) else event.ignore()


def build_app(argv=None, options: Options | None = None):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or [])
    theme.apply(app)
    win = MainWindow(options=options)
    return app, win


# --- faithful-skin launch (what the installed app runs) ----------------------
# The shipped app wears the pixel-faithful official skin (Droid Sans, the real
# QSS/palette, textures). Its five DSP tabs are the faithful read-only view; our
# additive tabs (Connection, Configuration, Diagnostics & Health, Firmware) ride
# the same single Engine so a future live connect feeds them all. Sending stays
# gated exactly as before -- the skin transmits nothing.
def official_extra_tabs(engine):
    from . import theme as _theme
    from .screens import (ConnectionScreen, ConfigurationScreen, DiagnosticsHealthScreen,
                          FirmwareScreen)
    specs = [('Connection', ConnectionScreen), ('Configuration', ConfigurationScreen),
             ('Diagnostics & Health', DiagnosticsHealthScreen), ('Firmware', FirmwareScreen)]

    def wrap(cls):
        def make():
            w = cls(engine)
            holder = QtWidgets.QWidget(); holder.setObjectName('extraHolder')
            holder.setStyleSheet('QWidget#extraHolder { background: transparent; }' + _theme.QSS)
            lay = QtWidgets.QVBoxLayout(holder); lay.setContentsMargins(18, 14, 18, 14); lay.addWidget(w)
            return holder
        return make
    return [(label, wrap(cls)) for label, cls in specs]


def build_official_app(argv=None):
    """Build the QApplication + faithful window on one shared Engine."""
    # Crisp fractional Windows scaling (125 % / 150 %): pass the real factor through
    # rather than rounding it. Must be set before the QApplication exists.
    try:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    from .official.window import OfficialMainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or [])
    engine = Engine()
    win = OfficialMainWindow(official_extra_tabs(engine), engine=engine)
    return app, win


def main(argv=None):  # pragma: no cover - needs a display
    import sys
    app, win = build_official_app(sys.argv)
    win.show()
    sys.exit(app.exec())
