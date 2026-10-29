"""Canvas: a widget that paints its controls at exact (sub-pixel) reference
positions. Each control is a small item object with a float rect, a paint()
and click/drag/wheel behaviour. A value item changes its own value the way the
Qt widget it mimics would (spin arrows step, LEDs toggle, sliders drag, combos
pick) and then calls its bound `on_change` callback; the screen that bound it
edits the model and pushes the change to the device (engine.push)."""
from __future__ import annotations

from ..qt import QtCore, QtGui, QtWidgets, Qt
from . import skin as S


class Item:
    tooltip = ''
    children = ()
    canvas = None
    on_change = None          # bound by the screen: called with the new value

    def __init__(self, r: QtCore.QRectF, tooltip=''):
        self.r = r
        self.visible = True
        # live look only (QSS :hover / :pressed); part = sub-control under the mouse
        self.hover = self.pressed = False
        self.part = None
        if tooltip:
            self.tooltip = tooltip

    def bind(self, fn):
        """Attach the change callback; returns the item so add(...).bind(fn) reads well."""
        self.on_change = fn
        return self

    def fire(self, value):
        if self.on_change is not None:
            self.on_change(value)

    @staticmethod
    def pos_of(e):
        return e.position() if hasattr(e, 'position') else QtCore.QPointF(e.pos())

    def drag(self, pos):
        """Mouse moved with the button held on this item."""

    def release(self, pos):
        """Button released after a press on this item."""

    @property
    def hoverable(self):
        return False

    def part_at(self, pos: QtCore.QPointF):
        return None

    def state(self, part=None):
        """'pressed' / 'hover' / None for the whole control or one sub-control."""
        if part is not None and self.part != part:
            return None
        return 'pressed' if self.pressed else ('hover' if self.hover else None)

    def paint(self, p):
        pass

    def click(self, pos):
        return False


class Label(Item):
    def __init__(self, x, cy, s, px=12, bold=False, color=S.TEXT_LABEL, align='left', shadow=True):
        super().__init__(QtCore.QRectF(x, cy - px, 1, px * 2))
        self.x, self.cy, self.s, self.px, self.bold = x, cy, s, px, bold
        self.color, self.align, self.shadow = color, align, shadow

    def paint(self, p):
        S.text(p, self.x, self.cy, self.s, self.px, self.bold, color=self.color,
               align=self.align, shadow=self.shadow)


class Spin(Item):
    """QDoubleSpinBox look. active=True: red frame; False: official disabled look."""
    def __init__(self, r, value, active=True, align='left', px=12, tooltip='', rule=None, number=None,
                 suffix=None):
        super().__init__(r, tooltip)
        self.value, self.active, self.align, self.px = value, active, align, px
        self.suffix = suffix if suffix is not None else (rule.suffix if rule is not None else '')
        # Optional numeric behaviour from uirules.SpinRule (QDoubleSpinBox range,
        # single step, decimals, suffix). The skin only displays; set_number()
        # clamps/quantises exactly as the official spin box would.
        self.rule, self.number = rule, number
        if rule is not None and number is not None:
            self.set_number(number)

    def set_number(self, v):
        from ... import uirules as R
        self.number = R.quantise(self.rule, float(v))
        self.value = ('%.*f' % (self.rule.decimals, self.number)) + self.suffix
        return self.number

    def step_number(self, direction):
        """Up/down arrow: one single-step, clamped to the range."""
        return self.set_number(self.number + direction * self.rule.step)

    def step(self, direction):
        """Arrow click / wheel notch: step and report, like QDoubleSpinBox.valueChanged."""
        if not self.active or self.rule is None or self.number is None:
            return False
        before = self.number
        v = self.step_number(direction)
        if v != before:
            self.fire(v)
        return True

    def click(self, e):
        part = self.part_at(self.pos_of(e))
        if part == 'up':
            return self.step(+1)
        if part == 'down':
            return self.step(-1)
        return False

    @property
    def hoverable(self):
        return self.active

    def _col(self):
        w = 16 if self.active else 15
        return QtCore.QRectF(self.r.right() - 3 - w, self.r.top() + 3, w, self.r.height() - 6)

    def part_at(self, pos):
        col = self._col()
        if not col.contains(pos):
            return 'field'
        return 'up' if pos.y() < col.center().y() else 'down'

    def paint(self, p):
        S.field(p, self.r, self.active, hover=self.hover or self.pressed)
        col = S.spin_buttons(p, self.r, self.active, self.state('up'), self.state('down'))
        c = QtGui.QColor(250, 250, 250) if self.active else S.TEXT_FIELD_DISABLED
        cy = self.r.center().y()
        if self.align == 'center':
            S.text(p, (self.r.left() + col.left()) / 2, cy, self.value, self.px, color=c,
                   align='center', shadow=False)
        else:
            S.text(p, self.r.left() + S.D(14), cy, self.value, self.px, color=c, shadow=False)


class Combo(Item):
    def __init__(self, r, items, current, active=True, px=12, tooltip=''):
        super().__init__(r, tooltip)
        self.items, self.current, self.active, self.px = list(items), current, active, px

    @property
    def hoverable(self):
        return self.active

    def paint(self, p):
        S.field(p, self.r, self.active, red=S.RED_BORDER_COMBO, hover=self.hover or self.pressed)
        S.combo_button(p, self.r, self.active, self.state())
        c = QtGui.QColor(250, 250, 250) if self.active else S.TEXT_FIELD_DISABLED
        S.text(p, self.r.left() + S.D(12), self.r.center().y(), self.current, self.px, color=c,
               shadow=False)

    def click(self, e):
        if self.active and self.canvas is not None:
            self.canvas.open_popup(self)
        return False

    def select(self, text):
        """Popup pick: QComboBox.currentIndexChanged -> on_change(index)."""
        if text in self.items and text != self.current:
            self.current = text
            self.fire(self.items.index(text))
            return True
        return False


class Led(Item):
    """LED checkbox: red = ON, grey = OFF, label to the right."""
    def __init__(self, cx, cy, on, label='', label_x=None, px=12, enabled=True, tooltip=''):
        d = S.D(28)
        super().__init__(QtCore.QRectF(cx - d / 2, cy - d / 2, d, d), tooltip)
        self.cx, self.cy, self.on, self.label = cx, cy, on, label
        self.label_x = label_x if label_x is not None else cx + S.D(28)
        self.px, self.enabled = px, enabled

    @property
    def hoverable(self):
        return self.enabled

    def paint(self, p):
        S.led(p, self.cx, self.cy, self.on, enabled=self.enabled, hover=self.hover or self.pressed)
        if self.label:
            S.text(p, self.label_x, self.cy, self.label, self.px,
                   color=S.TEXT_LABEL if self.enabled else S.TEXT_DIM)

    def click(self, e):
        if not self.enabled:
            return False
        self.on = not self.on
        self.fire(self.on)
        return True


class Switch(Item):
    def __init__(self, r, on=False, enabled=True, tooltip=''):
        super().__init__(r, tooltip)
        self.on, self.enabled = on, enabled

    @property
    def hoverable(self):
        return self.enabled

    def paint(self, p):
        S.switch(p, self.r, self.on, self.enabled)

    def click(self, e):
        if not self.enabled:
            return False
        self.on = not self.on
        self.fire(self.on)
        return True


class Pill(Item):
    def __init__(self, r, label, enabled=True, px=11, split=False, tooltip=''):
        super().__init__(r, tooltip)
        self.label, self.enabled, self.px, self.split = label, enabled, px, split

    @property
    def hoverable(self):
        return self.enabled

    def paint(self, p):
        S.pill_button(p, self.r, self.label, self.enabled, self.px, self.split, self.state())

    def click(self, e):
        if not self.enabled:
            return False
        self.fire(None)
        return True


class HSlider(Item):
    """QSlider look. frac 0..1 along the groove; drag or click-to-jump, and the
    bound callback fires on release with the final fraction (one device write per
    gesture, not one per pixel)."""
    def __init__(self, x0, x1, cy, frac, enabled=True, tooltip=''):
        super().__init__(QtCore.QRectF(x0, cy - S.D(13), x1 - x0, S.D(26)), tooltip)
        self.x0, self.x1, self.cy, self.frac, self.enabled = x0, x1, cy, frac, enabled
        self._start = None

    @property
    def hoverable(self):
        return self.enabled

    def _handle(self):
        cx = self.x0 + 8 + (self.x1 - self.x0 - 16) * self.frac
        return QtCore.QRectF(cx - 8, self.cy - 8, 16, 16)

    def part_at(self, pos):
        return 'handle' if self._handle().contains(pos) else 'groove'

    def _frac_at(self, pos):
        span = max(1.0, self.x1 - self.x0 - 16)
        return max(0.0, min(1.0, (pos.x() - self.x0 - 8) / span))

    def click(self, e):
        if not self.enabled:
            return False
        self._start = self.frac
        self.frac = self._frac_at(self.pos_of(e))
        return True

    def drag(self, pos):
        if self.enabled and self._start is not None:
            self.frac = self._frac_at(pos)

    def release(self, pos):
        if self._start is None:
            return
        start, self._start = self._start, None
        if self.frac != start:
            self.fire(self.frac)

    def paint(self, p):
        S.hslider(p, self.x0, self.x1, self.cy, self.frac, self.enabled, self.state('handle'))


POPUP_QSS = """
QListWidget { border: 3px solid #D12323; color: white; outline: 0px;
    font-family: "Droid Sans"; font-size: 12px;
    background: qlineargradient(spread:pad, x1:0 y1:0, x2:0 y2:1, stop:0 #555, stop:1 #000); }
QListWidget::item { padding: 1px 0px 1px 5px; }
QListWidget::item:hover { background-color: #630600; }
QListWidget::item:selected { color: white; background: qlineargradient(spread:pad, x1:0 y1:0,
    x2:0 y2:1, stop: 0.00 #D12323, stop: 1.00 #780800); }
"""


class ComboPopup(QtWidgets.QListWidget):
    """Open-dropdown look (QComboBox QAbstractItemView). Picking an entry closes
    the list and selects it on the combo (which fires its bound callback)."""

    def __init__(self, combo, parent):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setStyleSheet(POPUP_QSS)
        self.setMouseTracking(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.combo = combo
        for it in combo.items:
            self.addItem(it)
        if combo.current in combo.items:
            self.setCurrentRow(combo.items.index(combo.current))
        self.itemClicked.connect(self._pick)

    def _pick(self, it):
        self.close()
        self.combo.select(it.text())
        p = self.parent()
        if p is not None:
            p.update()
        row_h = self.sizeHintForRow(0) if self.count() else 18
        self.resize(int(combo.r.width()), int(row_h * min(self.count(), 16) + 6))

    def closeEvent(self, e):
        self.combo.pressed = False
        p = self.parent()
        if p is not None:
            # Record when this popup closed so a click on the combo's own arrow that
            # dismissed it isn't immediately re-opened (makes the arrow toggle it
            # closed, like the official app). See Canvas.open_popup.
            import time as _t
            p._popup_closed_at = _t.monotonic()
            p.popup = None
            p.update()
        super().closeEvent(e)


class Canvas(QtWidgets.QWidget):
    """Transparent widget; subclasses fill self.items and may override paint_static.
    Tracks the live hover/pressed look of each control (no value is ever changed)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[Item] = []
        self._hot = None
        self._drag = None             # item receiving drag/release after a press
        self.popup = None
        self._popup_closed_at = 0.0   # guards the dropdown-arrow toggle (see open_popup)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def add(self, item):
        item.canvas = self
        for c in item.children:
            c.canvas = self
        self.items.append(item)
        return item

    def paint_static(self, p):
        pass

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        self.paint_static(p)
        for it in self.items:
            if it.visible:
                p.save(); it.paint(p); p.restore()
        p.end()

    def _hit(self, pos):
        pf = QtCore.QPointF(pos)
        for it in reversed(self.items):
            if it.visible and it.r.contains(pf):
                for c in it.children:
                    if c.r.contains(pf):
                        return c
                return it
        return None

    @staticmethod
    def _pos(e):
        return e.position() if hasattr(e, 'position') else QtCore.QPointF(e.pos())

    def set_hot(self, it, part=None):
        """Make `it` the hovered control (None clears); also used to stage demo frames."""
        if it is not None and not it.hoverable:
            it = None
        if self._hot is not None and self._hot is not it:
            self._hot.hover = self._hot.pressed = False
            self._hot.part = None
        if it is not None:
            it.hover, it.part = True, part
        changed = it is not self._hot or (it is not None and part != it.part)
        self._hot = it
        self.update()
        return changed

    def mouseMoveEvent(self, e):
        pos = self._pos(e)
        if self._drag is not None:
            self._drag.drag(pos)
            self.update()
            return
        it = self._hit(pos.toPoint())
        part = it.part_at(pos) if it is not None else None
        if it is not self._hot or (it is not None and it.part != part):
            self.set_hot(it, part)

    def leaveEvent(self, e):
        self.set_hot(None)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        pos = self._pos(e)
        it = self._hit(pos.toPoint())
        if it is not None and it.hoverable:
            self.set_hot(it, it.part_at(pos))
            it.pressed = True
        self._drag = None
        if it is not None and it.click(e):
            self._drag = it if it in self.items or any(it in x.children for x in self.items) else None
        self.update()

    def mouseReleaseEvent(self, e):
        pos = self._pos(e)
        if self._drag is not None:
            d, self._drag = self._drag, None
            d.release(pos)
        if self._hot is not None and not isinstance(self.popup, ComboPopup):
            self._hot.pressed = False
        self.update()

    def wheelEvent(self, e):
        """A wheel notch over an active spin box steps it, like QAbstractSpinBox."""
        pos = self._pos(e)
        it = self._hit(pos.toPoint())
        if isinstance(it, Spin) and it.active:
            dy = e.angleDelta().y()
            if dy:
                it.step(+1 if dy > 0 else -1)
                self.update()
                e.accept()
                return
        super().wheelEvent(e)

    def open_popup(self, combo):
        """Open the combobox list under `combo` (read-only). A click on the arrow
        that just dismissed an open popup must not reopen it, so ignore an open
        request that lands within a moment of the last close (arrow = toggle)."""
        import time as _t
        if _t.monotonic() - self._popup_closed_at < 0.25:
            return None
        combo.pressed = True
        self.popup = ComboPopup(combo, self)
        self.popup.move(self.mapToGlobal(QtCore.QPoint(int(combo.r.left()), int(combo.r.bottom()))))
        self.popup.show()
        self.update()
        return self.popup

    def event(self, e):
        if e.type() == QtCore.QEvent.ToolTip:
            it = self._hit(e.pos())
            if it is not None and it.tooltip:
                QtWidgets.QToolTip.showText(e.globalPos(), it.tooltip, self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(e)
