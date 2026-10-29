"""Fonts, bitmaps, colours and painting primitives of the official skin.

Reference pixels: the official window was captured at 150 % Windows scaling, so
one Qt logical pixel = 1.5 reference pixels. Helpers:
  D(v)        length in reference px -> logical px
  X(x), Y(y)  reference frame coords -> stage-local logical coords
              (the stage, i.e. the brushed panel under the tab bar, starts at (23, 226))
  CX, CY      reference frame coords -> central-widget logical coords (window client
              starts at (6, 52); the central widget sits below the 23-px menu bar)
Colours, borders, sizes and fonts follow the app's own compiled stylesheet
(:/qss/mobridge_dsp_style.qss, extracted to mobridge-app-ui/decomp/); QSS px are logical px.
"""
from __future__ import annotations
import os

from ..qt import QtCore, QtGui, Qt

HERE = os.path.dirname(__file__)
REF = 1.5
STAGE_ORIGIN = (23.0, 226.0)
MENU_H = 23                       # menu bar height, logical px (35 reference px)
CLIENT_ORIGIN = (6.0, 52.0 + MENU_H * 1.5)   # central widget origin, below the menu bar


def D(v):
    return v / REF


def X(x):
    return (x - STAGE_ORIGIN[0]) / REF


def Y(y):
    return (y - STAGE_ORIGIN[1]) / REF


def CX(x):
    return (x - CLIENT_ORIGIN[0]) / REF


def CY(y):
    return (y - CLIENT_ORIGIN[1]) / REF


def rect(x0, y0, x1, y1, client=False):
    """Reference-pixel box -> logical QRectF (stage-local, or client-local)."""
    fx, fy = (CX, CY) if client else (X, Y)
    return QtCore.QRectF(fx(x0), fy(y0), D(x1 - x0), D(y1 - y0))


def irect(r: QtCore.QRectF) -> QtCore.QRect:
    return QtCore.QRect(round(r.x()), round(r.y()), round(r.width()), round(r.height()))


# --- colours (from mobridge_dsp_style.qss) -----------------------------------
RED_BORDER = QtGui.QColor('#D12323')         # QSpinBox / QComboBox border: 3px solid #D12323
RED_BORDER_COMBO = RED_BORDER
RED_HOVER = QtGui.QColor('#DD3131')
RED_ACCENT = QtGui.QColor('#D12323')
TEXT = QtGui.QColor(236, 236, 236)
TEXT_LABEL = QtGui.QColor('#D4D4D4')         # QLabel / QCheckBox color
TEXT_DIM = QtGui.QColor('#8f8f8f')           # QLabel:disabled
TEXT_FIELD_DISABLED = QtGui.QColor('gray')   # QSpinBox:disabled color
GROUP_BORDER = QtGui.QColor('#606060')       # QGroupBox border: 2px solid #606060, radius 5
DIVIDER = QtGui.QColor('gray')               # QFrame[frameShape] dividers
EQ_ORANGE = QtGui.QColor(240, 94, 50)
GRID_SOLID = QtGui.QColor(160, 160, 164)
GRID_DASH = QtGui.QColor(150, 150, 150)

# --- fonts -------------------------------------------------------------------
FAMILY = 'Droid Sans'          # bundled by the official app (:font/DroidSans.ttf)
MENU_FAMILY = 'Segoe UI'       # native menu bar font on Windows
_fonts_loaded = False


def load_fonts():
    global _fonts_loaded, FAMILY
    if _fonts_loaded:
        return
    _fonts_loaded = True
    fam = None
    for fn in ('DroidSans.ttf', 'DroidSans-Bold.ttf'):
        fid = QtGui.QFontDatabase.addApplicationFont(os.path.join(HERE, 'fonts', fn))
        if fid >= 0 and fam is None:
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid)
            fam = fams[0] if fams else None
    if fam:
        FAMILY = fam
    # menu-bar fallback where the real Segoe UI is missing (Selawik = Segoe UI metrics, OFL)
    if 'Segoe UI' not in QtGui.QFontDatabase.families():
        QtGui.QFontDatabase.addApplicationFont(os.path.join(HERE, 'fonts', 'selawk.ttf'))


def font(px, bold=False, italic=False, family=None):
    f = QtGui.QFont(family or FAMILY)
    f.setPixelSize(int(px))
    f.setBold(bold)
    f.setItalic(italic)
    f.setHintingPreference(QtGui.QFont.PreferNoHinting)
    return f


# --- pixmaps -----------------------------------------------------------------
_pix = {}


def pix(name) -> QtGui.QPixmap:
    p = _pix.get(name)
    if p is None:
        p = QtGui.QPixmap(os.path.join(HERE, 'assets', name))
        _pix[name] = p
    return p


def asset_path(name):
    return os.path.join(HERE, 'assets', name)


# --- text --------------------------------------------------------------------
def text(p: QtGui.QPainter, x, cy, s, px=12, bold=False, italic=False, color=TEXT_LABEL,
         align='left', shadow=True, family=None):
    """Draw s with its cap height centred on cy (logical). Returns the advance width."""
    f = font(px, bold, italic, family)
    p.setFont(f)
    fm = QtGui.QFontMetricsF(f)
    w = fm.horizontalAdvance(s)
    if align == 'right':
        x -= w
    elif align == 'center':
        x -= w / 2
    base = cy + fm.capHeight() / 2
    if shadow:
        p.setPen(QtGui.QColor(0, 0, 0, 70))
        p.drawText(QtCore.QPointF(x + 1.7, base + 1.7), s)
        p.setPen(QtGui.QColor(0, 0, 0, 110))
        p.drawText(QtCore.QPointF(x + 1.0, base + 1.0), s)
    p.setPen(color)
    p.drawText(QtCore.QPointF(x, base), s)
    return w


def runs(p, x, cy, parts, px=12, color=TEXT_LABEL, shadow=True):
    """Draw a line made of (text, style) runs; style contains 'b' and/or 'i'."""
    for s, style in parts:
        x += text(p, x, cy, s, px, 'b' in style, 'i' in style,
                  QtGui.QColor(250, 250, 250) if 'b' in style else color, shadow=shadow)
    return x


# --- primitives --------------------------------------------------------------
def vgrad(r: QtCore.QRectF, stops):
    g = QtGui.QLinearGradient(r.topLeft(), r.bottomLeft())
    for t, c in stops:
        g.setColorAt(t, QtGui.QColor(*c) if isinstance(c, tuple) else QtGui.QColor(c))
    return QtGui.QBrush(g)


def field(p, r: QtCore.QRectF, active=True, red=RED_BORDER, hover=False):
    """Spin/combo body: red 4-ref-px frame + black gloss (active), or grey (disabled)."""
    # QSS: border 3px solid #D12323, radius 3, background #555 -> #000;
    # disabled: border 3px solid #333, background #444 -> #333
    p.setPen(Qt.NoPen)
    # QSpinBox:hover / QComboBox:hover / QLineEdit:hover: border 3px solid #DD3131
    p.setBrush((RED_HOVER if hover else red) if active else QtGui.QColor('#333'))
    p.drawRoundedRect(r, 3, 3)
    inner = r.adjusted(3, 3, -3, -3)
    p.setBrush(vgrad(inner, [(0, '#555'), (1, '#000')] if active else [(0, '#444'), (1, '#333')]))
    p.drawRect(inner)


def _tri(p, cx, cy, w, h, up, color):
    path = QtGui.QPainterPath()
    if up:
        path.moveTo(cx - w / 2, cy + h / 2); path.lineTo(cx + w / 2, cy + h / 2); path.lineTo(cx, cy - h / 2)
    else:
        path.moveTo(cx - w / 2, cy - h / 2); path.lineTo(cx + w / 2, cy - h / 2); path.lineTo(cx, cy + h / 2)
    path.closeSubpath()
    p.fillPath(path, color)


def red_button(p, r: QtCore.QRectF):
    p.setPen(Qt.NoPen)
    p.setBrush(vgrad(r, [(0, (206, 0, 1)), (0.3, (180, 0, 2)), (0.42, (74, 0, 0)), (1, (70, 0, 2))]))
    p.drawRect(r)


def grey_button(p, r: QtCore.QRectF):
    p.setPen(Qt.NoPen)
    p.setBrush(vgrad(r, [(0, (80, 80, 83)), (1, (66, 66, 69))]))
    p.drawRect(r)


def _img(p, r: QtCore.QRectF, name):
    pm = pix(name)
    if pm.isNull():
        return False
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    p.drawPixmap(r, pm, QtCore.QRectF(pm.rect()))
    return True


def _state(base, active, state):
    """QSS image for a sub-control: <base>_{mobridge,hover_mobridge,pressed_mobridge,disabled}.png."""
    if not active:
        return base + '_disabled.png'
    return base + ('_%s_mobridge.png' % state if state else '_mobridge.png')


def spin_buttons(p, r: QtCore.QRectF, active=True, up_state=None, down_state=None):
    """Up/down column at the right of a spin field (r = whole field):
    QSpinBox::up-arrow / down-arrow images :/images/select_{up,down}_{mobridge,disabled}.png."""
    w = 16 if active else 15
    col = QtCore.QRectF(r.right() - 3 - w, r.top() + 3, w, r.height() - 6)
    half = col.height() / 2
    _img(p, QtCore.QRectF(col.left(), col.top(), w, half), _state('select_up', active, up_state))
    _img(p, QtCore.QRectF(col.left(), col.top() + half, w, half), _state('select_down', active, down_state))
    return col


def combo_button(p, r: QtCore.QRectF, active=True, state=None):
    """QComboBox::down-arrow image :/images/combobox_down_{mobridge,disabled}.png."""
    if active:
        b = QtCore.QRectF(r.right() - D(4) - D(22), r.top() + D(5), D(22), r.height() - D(9))
        _img(p, b, _state('combobox_down', True, state))
    else:
        b = QtCore.QRectF(r.right() - D(4) - D(25), r.top() + D(4), D(25), r.height() - D(8))
        _img(p, b, 'combobox_down_disabled.png')
    return b


def led(p, cx, cy, on, d=17, enabled=True, hover=False):   # QCheckBox::indicator 17x17
    if not enabled:
        name = 'checkbox_pressed_mobridge.png' if on else 'checkbox_disabled.png'
    elif hover:
        name = 'checkbox_pressed_hover_mobridge.png' if on else 'checkbox_off_hover_mobridge.png'
    else:
        name = 'checkbox_pressed_mobridge.png' if on else 'checkbox.png'
    _img(p, QtCore.QRectF(cx - d / 2, cy - d / 2, d, d), name)


def ball(p, cx, cy, d=16, red=True, state=None):    # QSlider::handle 16px, image 60x61
    _img(p, QtCore.QRectF(cx - d / 2, cy - d / 2, d, d * 61 / 60), _state('slider_handle', red, state))


def switch(p, r: QtCore.QRectF, on=False, enabled=True):
    """MUTE / option pill switch: grey track, white knob (off, left) or red knob (on, right)."""
    p.setPen(Qt.NoPen)
    track = QtCore.QRectF(r.left() + D(4), r.center().y() - D(9), r.width() - D(8), D(18))
    a = 255 if enabled else 120
    p.setBrush(vgrad(track, [(0, (118, 118, 118, a)), (1, (92, 92, 92, a))]))
    p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
    d = D(26)
    cx = r.right() - d / 2 if on else r.left() + d / 2
    k = QtCore.QRectF(cx - d / 2, r.center().y() - d / 2, d, d)
    if on:
        g = QtGui.QRadialGradient(k.center() - QtCore.QPointF(d * 0.15, d * 0.2), d * 0.65)
        g.setColorAt(0, QtGui.QColor(230, 20, 20)); g.setColorAt(1, QtGui.QColor(150, 0, 0))
    else:
        g = QtGui.QRadialGradient(k.center() - QtCore.QPointF(d * 0.15, d * 0.2), d * 0.65)
        g.setColorAt(0, QtGui.QColor(240, 240, 240, a)); g.setColorAt(1, QtGui.QColor(196, 196, 196, a))
    p.setBrush(QtGui.QBrush(g))
    p.drawEllipse(k)


def pill_button(p, r: QtCore.QRectF, label, enabled=True, px=11, split=False, state=None):
    """QPushButton: border-image :/images/button_small (78x32) or button_small_wide (165x36),
    color lightgrey, 11px Droid Sans; split = LeftToolButton (141) + RightToolButton (24)."""
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    col = QtGui.QColor('lightgrey') if enabled else QtGui.QColor('gray')
    if split:
        lw = r.width() * 141 / 165
        left = QtCore.QRectF(r.left(), r.top(), lw, r.height())
        right = QtCore.QRectF(r.left() + lw, r.top(), r.width() - lw, r.height())
        for rr, name in ((left, 'toolbuttonleft_small_wide'), (right, 'toolbuttonright_small_wide')):
            # hover/pressed art (*_hover_mobridge / *_pressed_mobridge) when extracted
            if not (enabled and state and _img(p, rr, '%s_%s_mobridge.png' % (name, state))):
                _img(p, rr, name + '.png')
        text(p, left.center().x(), r.center().y(), label, px, color=col,
             align='center', shadow=False)
        return
    wide = r.width() > 100
    name = 'button_small_wide' if wide else 'button_small'
    if not (enabled and state and _img(p, r, '%s_%s_mobridge.png' % (name, state))):
        _img(p, r, name + ('' if enabled else '_disabled') + '.png')
    text(p, r.center().x(), r.center().y(), label, px, color=col, align='center', shadow=False)


def group(p, r: QtCore.QRectF, title, px=12):
    """QGroupBox look: 2-ref-px grey rounded frame, title breaking the top edge."""
    f = font(px)
    tw = QtGui.QFontMetricsF(f).horizontalAdvance(title)
    tx = r.left() + 16                 # title left: 11px + padding 5px
    path = QtGui.QPainterPath()
    path.addRoundedRect(r, 5, 5)
    p.save()
    clip = QtGui.QRegion(irect(r.adjusted(-4, -4, 4, 4))) - QtGui.QRegion(
        irect(QtCore.QRectF(tx - D(8), r.top() - D(6), tw + D(16), D(12))))
    p.setClipRegion(clip)
    p.setBrush(Qt.NoBrush)
    p.setPen(QtGui.QPen(GROUP_BORDER, 2))
    p.drawPath(path)
    p.restore()
    text(p, tx, r.top(), title, px)


def vsep(p, x, y0, y1):
    """Vertical pane separator (light line with a dark shadow)."""
    p.fillRect(QtCore.QRectF(x - D(1), y0, D(2.5), y1 - y0), DIVIDER)


def hslider(p, x0, x1, cy, frac, enabled=True, state=None):
    # QSlider::groove:horizontal: 7px, #8b8b8b / #d7d7d7 / #8b8b8b, radius 3
    track = QtCore.QRectF(x0, cy - 3.5, x1 - x0, 7)
    p.setPen(Qt.NoPen)
    p.setBrush(vgrad(track, [(0, '#8b8b8b'), (0.5, '#d7d7d7'), (1, '#8b8b8b')]))
    p.drawRoundedRect(track, 3, 3)
    ball(p, x0 + 8 + (x1 - x0 - 16) * frac, cy, 16, red=enabled, state=state)
