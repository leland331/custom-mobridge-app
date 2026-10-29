"""Faithful UI widgets matching the official mObridge app: cabin speaker
selector, red/grey LED checkboxes, red-bordered value fields, and the EQ graph.
Everything renders offline; value controls are created disabled (parity)."""
from __future__ import annotations
import os

from .qt import QtWidgets, QtGui, QtCore, Qt
from . import theme

ASSETS = os.path.join(os.path.dirname(__file__), 'assets')


def asset(name: str) -> str:
    return os.path.join(ASSETS, name)


def pix(name: str) -> QtGui.QPixmap:
    return QtGui.QPixmap(asset(name))


def red_spin(suffix=' dB', decimals=1, lo=-100.0, hi=100.0, val=0.0, step=0.1, width=92):
    sp = QtWidgets.QDoubleSpinBox()
    sp.setSuffix(suffix); sp.setDecimals(decimals)
    sp.setRange(lo, hi); sp.setSingleStep(step); sp.setValue(val)
    sp.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
    sp.setFixedWidth(width); sp.setAlignment(Qt.AlignLeft)
    sp.setEnabled(False)   # parity: gated until proven
    return sp


class LedCheck(QtWidgets.QWidget):
    """Red = ON, grey = OFF LED checkbox with a label to its right (official app)."""
    def __init__(self, text='', on=False, parent=None):
        super().__init__(parent)
        row = QtWidgets.QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(7)
        self._on = on
        self.led = QtWidgets.QLabel()
        self.led.setFixedSize(18, 18)
        self._red = pix('led_red.png').scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._grey = pix('led_grey.png').scaled(18, 18, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._refresh()
        row.addWidget(self.led)
        if text:
            row.addWidget(QtWidgets.QLabel(text))
        row.addStretch(1)

    def _refresh(self):
        self.led.setPixmap(self._red if self._on else self._grey)

    def setOn(self, on):
        self._on = bool(on); self._refresh()


# Cabin positions (fraction of the square) for the reference mapped speakers + numbers.
# Mirrors the official SpeakerSelector layout (fronts upper, rears lower, sub bottom).
_POS = [
    ('centermid',      0.50, 0.08, 3),
    ('frontleftwoofer',0.14, 0.30, 11),
    ('frontrightwoofer',0.86, 0.30, 12),
    ('rearleftwoofer', 0.14, 0.74, 1),
    ('rearrightwoofer',0.86, 0.74, 2),
    ('rightsubwoofer', 0.50, 0.93, 4),
]


class CabinSelector(QtWidgets.QFrame):
    """Circular cabin image with the mapped speakers around it, channel numbers,
    selected speaker in red (click to select). Matches the official selector."""
    selected = QtCore.Signal(str)   # object_name

    def __init__(self, only_mapped=False, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 330)
        self._cabin = pix('cabin.png')
        self._spk = pix('spkr.png')
        self._spk_red = pix('spkr_red.png')
        self._cur = 'rightsubwoofer'   # sub selected like the reference frame
        self._hit = []

    def current(self):
        return self._cur

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        side = min(w, h - 20)
        ox, oy = (w - side) // 2, 0
        # circular cabin
        path = QtGui.QPainterPath(); path.addEllipse(ox + side*0.22, oy + side*0.05, side*0.56, side*0.82)
        p.save(); p.setClipPath(path)
        if not self._cabin.isNull():
            p.drawPixmap(int(ox + side*0.22), int(oy + side*0.05), int(side*0.56), int(side*0.82),
                         self._cabin)
        p.restore()
        p.setPen(QtGui.QPen(QtGui.QColor('#4a4a4a'), 1)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(int(ox + side*0.22), int(oy + side*0.05), int(side*0.56), int(side*0.82))
        self._hit = []
        sw = int(side*0.16)
        f = QtGui.QFont('Segoe UI'); f.setPointSize(11); f.setBold(True); p.setFont(f)
        for obj, fx, fy, num in _POS:
            cx = int(ox + fx*side); cy = int(oy + fy*side)
            icon = self._spk_red if obj == self._cur else self._spk
            if not icon.isNull():
                sc = icon.scaled(sw, sw, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(cx - sc.width()//2, cy - sc.height()//2, sc)
            p.setPen(QtGui.QColor('#f0f0f0'))
            p.drawText(cx + sw//2 - 4, cy - sw//2 + 14, str(num))
            self._hit.append((obj, QtCore.QRect(cx - sw//2, cy - sw//2, sw, sw)))
        p.end()

    def mousePressEvent(self, e):
        for obj, rect in self._hit:
            if rect.contains(e.pos()):
                self._cur = obj; self.update(); self.selected.emit(obj); return


class EqGraph(QtWidgets.QFrame):
    """Master/Channel EQ plot: log frequency grid 20 Hz..20 kHz, +9..-27 dB,
    orange response curve (flat at the drawn gain)."""
    def __init__(self, gain_db=0.0, parent=None):
        super().__init__(parent)
        self.setObjectName('eqGraph')
        self.setMinimumHeight(300)
        self.gain_db = gain_db

    def _x(self, f, w):
        import math
        a, b = math.log10(20), math.log10(20000)
        return int((math.log10(f) - a) / (b - a) * (w - 70) + 55)

    def _y(self, db, h):
        return int((9 - db) / (9 - (-27)) * (h - 20) + 6)

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QtGui.QColor('#161616'))
        p.setFont(QtGui.QFont('Segoe UI', 8))
        # vertical freq grid
        for f in (20,30,40,50,70,100,200,300,400,500,700,1000,2000,3000,5000,7000,10000,20000):
            x = self._x(f, w)
            major = f in (50,100,500,1000,5000,10000,20000)
            p.setPen(QtGui.QPen(QtGui.QColor('#3a3a3a' if major else '#262626'), 1))
            p.drawLine(x, 4, x, h-4)
            if major:
                p.setPen(QtGui.QColor('#8f8f8f'))
                lab = ('%dkHz'%(f//1000)) if f>=1000 else ('%dHz'%f)
                p.drawText(x-14, 14, lab)
        # horizontal dB grid
        for db in range(9, -28, -3):
            y = self._y(db, h)
            p.setPen(QtGui.QPen(QtGui.QColor('#333' if db==0 else '#242424'), 1))
            p.drawLine(50, y, w-6, y)
            p.setPen(QtGui.QColor('#8f8f8f')); p.drawText(6, y+4, '%d dB'%db)
        # response curve (flat)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.ORANGE), 2))
        y = self._y(self.gain_db, h)
        p.drawLine(55, y, w-8, y)
        # frame
        p.setPen(QtGui.QPen(QtGui.QColor('#5a5a5a'), 1)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(50, 4, w-58, h-10, 6, 6)
        p.end()
