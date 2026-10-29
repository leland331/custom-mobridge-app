"""The official mObridge tabs, pixel-faithful to the connected app (UI_SPEC §16).
Value controls render disabled (parity send-gate); layout/labels/assets match."""
from __future__ import annotations

from .qt import QtWidgets, Qt
from . import faithful as F
from .. import channel_map as cm

OUTPUT_KINDS = (['No Mapping'] + ['CH%d [Output Channel]' % n for n in range(1, 9)]
                + ['CH9 [RCA 1]', 'CH10 [RCA 2]', 'CH11 [TOSLINK Left]', 'CH12 [TOSLINK Right]'])


def _hline():
    f = QtWidgets.QFrame(); f.setFrameShape(QtWidgets.QFrame.HLine)
    f.setStyleSheet('color:#3a3a3a; background:#3a3a3a; max-height:1px;'); return f


def _blocked(text):
    l = QtWidgets.QLabel('🔒 ' + text); l.setObjectName('blockedNote'); l.setWordWrap(True); return l


class _Stage(QtWidgets.QWidget):
    """A rounded dark stage panel, like the app's brushed-metal content area."""
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setObjectName('stage')
        self.root = QtWidgets.QVBoxLayout(self); self.root.setContentsMargins(22, 18, 22, 14)


class ConfigMasterEqScreen(_Stage):
    title = 'CONFIG & MASTER EQ'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        top = QtWidgets.QHBoxLayout(); top.setSpacing(24)

        # left: cabin speaker selector
        self.cabin = F.CabinSelector(); self.cabin.setFixedWidth(330)
        top.addWidget(self.cabin)

        # middle: selected speaker + mapping
        mid = QtWidgets.QVBoxLayout(); mid.setSpacing(14)
        hdr = QtWidgets.QHBoxLayout()
        self.spkName = QtWidgets.QLabel('Right Sub Woofer'); self.spkName.setObjectName('speakerName')
        hdr.addWidget(self.spkName); hdr.addStretch(1)
        hdr.addWidget(QtWidgets.QLabel('MUTE'))
        mute = QtWidgets.QCheckBox(); mute.setEnabled(False); hdr.addWidget(mute)
        mid.addLayout(hdr)
        form = QtWidgets.QGridLayout(); form.setHorizontalSpacing(16); form.setVerticalSpacing(12)
        form.addWidget(QtWidgets.QLabel('Output Mapping'), 0, 0)
        combo = QtWidgets.QComboBox(); combo.setObjectName('mapCombo'); combo.addItems(OUTPUT_KINDS)
        combo.setCurrentText('CH4 [Output Channel]'); combo.setEnabled(False); combo.setMinimumWidth(240)
        form.addWidget(combo, 0, 1)
        form.addWidget(QtWidgets.QLabel('Input Mapping'), 1, 0)
        ins = QtWidgets.QHBoxLayout()
        ins.addWidget(F.LedCheck('Left', on=True)); ins.addWidget(F.LedCheck('Right', on=True)); ins.addStretch(1)
        wrap = QtWidgets.QWidget(); wrap.setLayout(ins); form.addWidget(wrap, 1, 1)
        mid.addLayout(form); mid.addStretch(1)
        top.addLayout(mid, 1)

        # right: Digital Inputs
        right = QtWidgets.QVBoxLayout(); right.setSpacing(12)
        di = QtWidgets.QLabel('Digital Inputs'); di.setObjectName('sectionTitle'); right.addWidget(di)
        gains = QtWidgets.QGroupBox('Gains'); gg = QtWidgets.QGridLayout(gains)
        for i, (name, val) in enumerate([('Stereo', 0.0), ('Prompt', 0.0), ('Beeps', -2.0),
                                         ('Phone', 0.0), ('Voice', 0.0)]):
            r, c = i % 3, (i // 3) * 2
            gg.addWidget(QtWidgets.QLabel(name), r, c)
            gg.addWidget(F.red_spin(' dB', 1, -60, 12, val), r, c + 1)
        right.addWidget(gains)
        md = QtWidgets.QGroupBox('Mono Delays'); ml = QtWidgets.QHBoxLayout(md)
        sld = QtWidgets.QSlider(Qt.Horizontal); sld.setRange(0, 100); sld.setValue(100); sld.setEnabled(False)
        ml.addWidget(sld, 1); ml.addWidget(F.red_spin(' ms', 1, 0, 10, 10.0))
        right.addWidget(md); right.addStretch(1)
        top.addLayout(right, 1)

        self.root.addLayout(top)
        self.root.addWidget(_hline())

        # Master EQ
        eqrow = QtWidgets.QHBoxLayout()
        gsl = QtWidgets.QSlider(Qt.Vertical); gsl.setRange(-27, 9); gsl.setValue(0); gsl.setEnabled(False)
        gsl.setFixedWidth(28)
        lab = QtWidgets.QLabel('MASTER EQ'); lab.setObjectName('groupLabel')
        left_eq = QtWidgets.QVBoxLayout(); left_eq.addWidget(lab); left_eq.addWidget(gsl, 1)
        eqrow.addLayout(left_eq)
        eqrow.addWidget(F.EqGraph(0.0), 1)
        self.root.addLayout(eqrow, 1)

        # bottom control row
        bottom = QtWidgets.QHBoxLayout(); bottom.setSpacing(10)
        for lbl, suf, val, en in [('GAIN', ' dB', 0.0, False), ('FREQ', ' Hz', 0.0, False),
                                  ('MAG', ' dB', 0.0, False), ('Q-FACTOR', '', 0.05, False)]:
            bottom.addWidget(QtWidgets.QLabel(lbl))
            bottom.addWidget(F.red_spin(suf, 2 if lbl in ('FREQ','MAG') else (2 if lbl=='Q-FACTOR' else 1), -100, 22000, val))
        bottom.addWidget(F.red_spin('', 2, 0, 10, 0.05))
        bottom.addWidget(QtWidgets.QLabel('Q-L/R')); bottom.addWidget(F.LedCheck('', on=True))
        bottom.addStretch(1)
        for t in ('✕', '↺', '⧉'):
            b = QtWidgets.QToolButton(); b.setText(t); b.setEnabled(False); bottom.addWidget(b)
        self.root.addLayout(bottom)


class ChannelEqScreen(_Stage):
    title = 'CHANNEL EQ'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        top = QtWidgets.QHBoxLayout(); top.setSpacing(24)
        self.cabin = F.CabinSelector(); self.cabin.setFixedWidth(330); top.addWidget(self.cabin)
        mid = QtWidgets.QVBoxLayout(); mid.setSpacing(12)
        name = QtWidgets.QLabel('Right Sub Woofer'); name.setObjectName('speakerName'); mid.addWidget(name)
        fg = QtWidgets.QGridLayout(); fg.setHorizontalSpacing(16); fg.setVerticalSpacing(10)
        fg.addWidget(QtWidgets.QLabel('Filter Type'), 0, 0)
        ft = QtWidgets.QComboBox(); ft.addItems(['None', 'Low Pass', 'High Pass', 'Band Pass']); ft.setEnabled(False)
        fg.addWidget(ft, 0, 1)
        fg.addWidget(QtWidgets.QLabel('Filter Model'), 1, 0)
        fm = QtWidgets.QComboBox(); fm.addItems(['Butterworth', 'Bessel', 'Chebychev', 'Linkwitz-Riley', 'Biquad']); fm.setEnabled(False)
        fg.addWidget(fm, 1, 1)
        for i, t in enumerate(['Invert Phase', 'XOVER L/R Link', 'Direct / Passthrough']):
            c = QtWidgets.QCheckBox(t); c.setEnabled(False); fg.addWidget(c, 2 + i, 0, 1, 2)
        mid.addLayout(fg); mid.addStretch(1); top.addLayout(mid, 1)
        self.root.addLayout(top); self.root.addWidget(_hline())
        eqrow = QtWidgets.QHBoxLayout()
        gsl = QtWidgets.QSlider(Qt.Vertical); gsl.setRange(-27, 9); gsl.setValue(-3); gsl.setEnabled(False); gsl.setFixedWidth(28)
        lv = QtWidgets.QVBoxLayout(); t = QtWidgets.QLabel('CHANNEL EQ'); t.setObjectName('groupLabel'); lv.addWidget(t); lv.addWidget(gsl, 1)
        eqrow.addLayout(lv); eqrow.addWidget(F.EqGraph(-3.0), 1)
        self.root.addLayout(eqrow, 1)


class _InstructionTab(_Stage):
    icon = ''; intro = ''; steps = ()
    headers = ('Channel', 'A', 'B')

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        row = QtWidgets.QHBoxLayout(); row.setSpacing(26)
        left = QtWidgets.QVBoxLayout()
        t = QtWidgets.QLabel(self.icon + '  ' + self.intro); t.setWordWrap(True); t.setObjectName('speakerName')
        t.setStyleSheet('font-size:18px;'); left.addWidget(t)
        for i, s in enumerate(self.steps, 1):
            l = QtWidgets.QLabel('%d.  %s' % (i, s)); l.setWordWrap(True); left.addWidget(l)
        left.addStretch(1)
        lw = QtWidgets.QWidget(); lw.setLayout(left); lw.setFixedWidth(420); row.addWidget(lw)

        table = QtWidgets.QTableWidget(len(cm.assigned_positions()), 3)
        table.setHorizontalHeaderLabels(list(self.headers))
        table.verticalHeader().setVisible(False); table.setEnabled(False)
        for r, (disp, slot) in enumerate(sorted(cm.assigned_positions(), key=lambda x: x[1])):
            table.setItem(r, 0, QtWidgets.QTableWidgetItem('%d: %s' % (slot, disp)))
            table.setItem(r, 1, QtWidgets.QTableWidgetItem(self._colb))
            table.setItem(r, 2, QtWidgets.QTableWidgetItem(self._colc))
        table.resizeColumnsToContents()
        row.addWidget(table, 1)
        self.root.addLayout(row, 1)
        btns = QtWidgets.QHBoxLayout(); btns.addStretch(1)
        for t2 in self._buttons:
            b = QtWidgets.QPushButton(t2); b.setObjectName('pill'); b.setEnabled(False); btns.addWidget(b)
        self.root.addLayout(btns)


class TimeAlignScreen(_InstructionTab):
    title = 'TIME ALIGN'; icon = '📏'
    intro = 'Time alignment adjusts the delays on each speaker according to a chosen listening point.'
    steps = ('Choose the listening point.', 'Measure the speaker distances.',
             'Calculate the time delays. Click the Calc. Time Delays button.',
             'Adjust the time delay manually to fine tune (optional).')
    headers = ('Channel', 'Distance', 'Calc. Delay'); _colb = '0.0 cm'; _colc = '0.00 ms'
    _buttons = ('RESET', 'CALC. TIME DELAYS')


class LimiterScreen(_InstructionTab):
    title = 'LIMITER'; icon = '〜'
    intro = 'The limiter uses Dynamic Range Compression (DRC) to prevent audio distortion caused by clipping.'
    steps = ('Show the signal level with Real-Time Analysis (RTA).',
             'Bring the channel close to the clipping point.',
             'Set the limiter slider to the found clipping point, in dBFS.')
    headers = ('Channel', 'Limit', 'RTA'); _colb = '0.0 dBFS'; _colc = '○'
    _buttons = ('RESET',)


class ScriptingScreen(_Stage):
    title = 'SCRIPTING'

    def __init__(self, engine, parent=None):
        super().__init__(engine, parent)
        bar = QtWidgets.QHBoxLayout()
        for t, en in [('Run script ...', True), ('Restart', False), ('Reset', False)]:
            b = QtWidgets.QPushButton(t); b.setObjectName('pill'); b.setEnabled(en); bar.addWidget(b)
        bar.addStretch(1); bar.addWidget(QtWidgets.QLabel('No script selected'))
        self.root.addLayout(bar)
        con = QtWidgets.QPlainTextEdit(); con.setObjectName('consoleLog'); con.setReadOnly(True)
        con.setStyleSheet('background:#2d2d2d;'); con.setPlainText('$ ')
        self.root.addWidget(con, 1)
