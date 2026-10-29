"""Reusable widgets that mirror the official app's custom controls
(EqualizerEditorForm/EqualizerGraphicsView, QSwitch, SpeakerSelector, QImprovedSlider).

Every value control here is created DISABLED (parity: no unconfirmed value is
sendable). `set_live(True)` is called only when the bound encoding is verified.
"""
from __future__ import annotations

import math

from .qt import QtCore, QtGui, QtWidgets, Qt


class QSwitch(QtWidgets.QCheckBox):
    """Pill toggle like the app's QSwitch. Purely visual via QSS below + paint."""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setObjectName('qswitch')
        self.setCursor(Qt.PointingHandCursor)


class EqGraph(QtWidgets.QFrame):
    """The EQ plot: log 20Hz-20kHz x-axis, +9..-27 dB y-axis, grid + labels.
    Matches EqualizerSceneGrid styling (fill #212121, dashed grid, decade labels)."""
    FMIN, FMAX = 20.0, 20000.0
    DBMAX, DBMIN = 9.0, -27.0
    DECADES = [50, 100, 500, 1000, 5000, 10000, 20000]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)

    def _x(self, f, w):
        return (math.log10(f) - math.log10(self.FMIN)) / (math.log10(self.FMAX) - math.log10(self.FMIN)) * w

    def _y(self, db, h):
        return (self.DBMAX - db) / (self.DBMAX - self.DBMIN) * h

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.fillRect(r, QtGui.QColor('#212121'))
        p.setPen(QtGui.QPen(QtGui.QColor('#3a3a3a'), 1))
        p.drawRect(r)
        w, h = r.width(), r.height()
        # vertical grid: each 1-9 per decade, labelled decades solid
        f = 20
        labelpen = QtGui.QColor('#a0a0a4')
        while f <= self.FMAX:
            x = int(r.left() + self._x(f, w))
            solid = f in self.DECADES
            p.setPen(QtGui.QPen(QtGui.QColor('#3d3d3d') if solid else QtGui.QColor('#2c2c2c'),
                                1, Qt.SolidLine if solid else Qt.DashLine))
            p.drawLine(x, r.top(), x, r.bottom())
            if solid:
                p.setPen(labelpen)
                lbl = ('%dk' % (f // 1000)) if f >= 1000 else str(f)
                p.drawText(x + 2, r.top() + 11, lbl)
            step = 10 ** int(math.log10(f))
            f += step
        # horizontal grid every 3 dB
        p.setPen(QtGui.QPen(QtGui.QColor('#2c2c2c'), 1, Qt.DashLine))
        db = self.DBMIN
        while db <= self.DBMAX:
            y = int(r.top() + self._y(db, h))
            p.setPen(QtGui.QPen(QtGui.QColor('#333'), 1, Qt.DashLine))
            p.drawLine(r.left(), y, r.right(), y)
            p.setPen(labelpen)
            p.drawText(r.right() - 26, y - 1, '%+d' % db)
            db += 3
        # a flat 0 dB reference curve (orange), the resulting "bins" (yellow)
        p.setPen(QtGui.QPen(QtGui.QColor('#ff672f'), 2))
        y0 = int(r.top() + self._y(0, h))
        p.drawLine(r.left(), y0, r.right(), y0)
        p.end()


class EqEditor(QtWidgets.QWidget):
    """EqualizerEditorForm: title, vertical gain slider, graph, control row.
    `title` is 'MASTER EQ' or 'CHANNEL EQ'. `watermark` adds the WATERMARK box."""
    def __init__(self, title='MASTER EQ', watermark=False, parent=None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        t = QtWidgets.QLabel(title); t.setObjectName('eqTitle'); root.addWidget(t)

        mid = QtWidgets.QHBoxLayout()
        self.gain_slider = QtWidgets.QSlider(Qt.Vertical); self.gain_slider.setObjectName('gainDbSlider')
        self.gain_slider.setFixedWidth(28); self.gain_slider.setEnabled(False)
        mid.addWidget(self.gain_slider)
        self.graph = EqGraph(); mid.addWidget(self.graph, 1)
        root.addLayout(mid, 1)

        row = QtWidgets.QHBoxLayout()

        def spin(label, suffix, dec, obj):
            box = QtWidgets.QVBoxLayout()
            lbl = QtWidgets.QLabel(label); lbl.setObjectName('eqFieldLabel')
            sp = QtWidgets.QDoubleSpinBox(); sp.setObjectName(obj)
            sp.setSuffix(suffix); sp.setDecimals(dec); sp.setEnabled(False)
            sp.setMinimumWidth(96)
            box.addWidget(lbl); box.addWidget(sp)
            row.addLayout(box)
            return sp

        self.gain = spin('GAIN', ' dB', 1, 'gainDbDoubleSpinBox')
        self.freq = spin('FREQ', ' Hz', 2, 'frequencySpinBox')
        self.mag = spin('MAG', ' dB', 2, 'magnitudeSpinBox')
        self.qL = spin('Q-FACTOR', '', 2, 'qFactorLeftSpinBox')
        self.qR = spin('', '', 2, 'qFactorRightSpinBox')
        self.qlink = QtWidgets.QCheckBox('Q-L/R'); self.qlink.setEnabled(False)
        row.addWidget(self.qlink)
        if watermark:
            self.watermark = QtWidgets.QCheckBox('WATERMARK'); self.watermark.setEnabled(False)
            row.addWidget(self.watermark)
        row.addStretch(1)

        btnframe = QtWidgets.QFrame(); btnframe.setObjectName('eqButtonFrame')
        bl = QtWidgets.QHBoxLayout(btnframe); bl.setContentsMargins(6, 4, 6, 4)
        for tip, obj in (('Delete point', 'deleteButton'), ('Clear all', 'resetToolButton'),
                         ('Copy EQ', 'copyToolButton')):
            b = QtWidgets.QToolButton(); b.setText({'deleteButton': '✕', 'resetToolButton': '⟲',
                                                    'copyToolButton': '⧉'}[obj])
            b.setToolTip(tip); b.setEnabled(False); bl.addWidget(b)
        row.addWidget(btnframe)
        root.addLayout(row)


class SpeakerSelector(QtWidgets.QFrame):
    """The cabin + speaker ring (SpeakerSelector). Placeholder positions until the
    slot->name table is read from the decompile; click selects, drives speakerNameLabel."""
    selected = QtCore.Signal(str) if hasattr(QtCore, 'Signal') else None

    POSITIONS = ['Front Left Tweeter', 'Front Right Tweeter', 'Front Left Door', 'Front Right Door',
                 'Rear Left Door', 'Rear Right Door', 'Rear Left Corner', 'Rear Right Corner',
                 'Left Sub', 'Right Sub', 'Front Left Full', 'Front Right Full']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('speakerSelector')
        self.setMinimumSize(260, 260)
        self._buttons = []
        self._name = self.POSITIONS[0]

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.rect()
        cx, cy = r.center().x(), r.center().y()
        rad = min(r.width(), r.height()) // 2 - 20
        p.setPen(QtGui.QPen(QtGui.QColor('#555'), 2))
        p.drawEllipse(QtCore.QPoint(cx, cy), rad, rad)
        p.setBrush(QtGui.QColor('#2a2a2a')); p.drawRoundedRect(cx - 42, cy - 60, 84, 120, 10, 10)
        n = len(self.POSITIONS)
        p.setPen(Qt.NoPen)
        for i in range(n):
            ang = 2 * math.pi * i / n - math.pi / 2
            x = cx + int(rad * math.cos(ang)); y = cy + int(rad * math.sin(ang))
            p.setBrush(QtGui.QColor('#d12323') if self.POSITIONS[i] == self._name else QtGui.QColor('#8a8a8a'))
            p.drawEllipse(QtCore.QPoint(x, y), 9, 9)
        p.end()

    def mousePressEvent(self, e):
        r = self.rect(); cx, cy = r.center().x(), r.center().y()
        rad = min(r.width(), r.height()) // 2 - 20
        n = len(self.POSITIONS)
        for i in range(n):
            ang = 2 * math.pi * i / n - math.pi / 2
            x = cx + int(rad * math.cos(ang)); y = cy + int(rad * math.sin(ang))
            if (e.position().x() - x) ** 2 + (e.position().y() - y) ** 2 <= 144:
                self._name = self.POSITIONS[i]; self.update()
                if self.selected:
                    self.selected.emit(self._name)
                break

    def current_name(self):
        return self._name
