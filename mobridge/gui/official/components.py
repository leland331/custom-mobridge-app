"""The two big shared components of the official app, as canvas items:
SpeakerSelector (cabin + speaker icons) and the EQ editor (gain slider, graph,
GAIN/FREQ/MAG/Q row, tool buttons). Coordinates in reference pixels."""
from __future__ import annotations
import math

from ..qt import QtCore, QtGui, Qt
from . import skin as S
from . import data
from ... import uirules as R
from .canvas import Item, Spin, Led

X, Y, D = S.X, S.Y, S.D

# icon heights in reference px for a side-facing speaker (rotated ones swap w/h)
_SIZE = {'big': 59.0, 'mid': 44.0, 'tiny': 34.0, 'sub': 64.0}
_ASPECT = 0.75     # the app draws spkr.png a little wider than its 205x310 aspect


class SpeakerSelector(Item):
    """Cabin ellipse with the speaker icons around it. mapped_only=True is the
    CHANNEL EQ variant (only speakers with an output channel are shown)."""

    CENTRE = (302.0, 415.5)
    RX, RY = 123.0, 115.5

    def __init__(self, selected='rightsubwoofer', mapped_only=False, on_select=None, channels=None):
        super().__init__(S.rect(40, 240, 590, 600))
        self.selected, self.mapped_only, self.on_select = selected, mapped_only, on_select
        # objectName -> displayed channel number (1-based) for the mapped speakers;
        # None = the reference device from data.py (offline preview).
        self.channels = channels if channels is not None else {o: c['ch'] for o, c in data.CHANNELS.items()}
        self._hits = []

    # --- painting
    def _cabin(self, p):
        cx, cy = X(self.CENTRE[0]), Y(self.CENTRE[1])
        rx, ry = D(self.RX), D(self.RY)
        outer = QtCore.QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(14, 14, 14))
        p.drawEllipse(outer.adjusted(-D(2), -D(2), D(2), D(2)))
        p.setBrush(QtGui.QColor(140, 140, 140))
        p.drawEllipse(outer)
        inner = outer.adjusted(D(3), D(3), -D(3), -D(3))
        p.setBrush(QtGui.QColor(20, 20, 20))
        p.drawEllipse(inner)
        path = QtGui.QPainterPath(); path.addEllipse(inner.adjusted(D(1), D(1), -D(1), -D(1)))
        p.save(); p.setClipPath(path)
        pm = S.pix('car_cabin.png')
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        # the cabin bitmap is square; it is drawn to the ring's height, centred
        side = 2 * ry * 1.03
        p.drawPixmap(QtCore.QRectF(cx - side / 2, cy - side / 2 + D(2), side, side), pm,
                     QtCore.QRectF(pm.rect()))
        p.restore()

    def _icon(self, p, obj, size, facing, centre, mapped):
        h = D(_SIZE[size]); w = h * _ASPECT
        cx, cy = X(centre[0]), Y(centre[1])
        sel = obj == self.selected
        pm = S.pix('spkr_highlight.png' if sel else 'spkr.png')
        p.save()
        p.translate(cx, cy)
        if facing == 'left':
            p.scale(-1, 1)
        elif facing == 'down':
            p.rotate(90)
        elif facing == 'up':
            p.rotate(-90)
        if not mapped and not sel:
            p.setOpacity(0.8)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        p.drawPixmap(QtCore.QRectF(-w / 2, -h / 2, w, h), pm, QtCore.QRectF(pm.rect()))
        p.restore()
        rot = facing in ('up', 'down')
        bw, bh = (h, w) if rot else (w, h)
        self._hits.append((obj, QtCore.QRectF(cx - bw / 2, cy - bh / 2, bw, bh)))

    def _number(self, p, size, facing, centre, ch):
        cx, cy = centre
        if facing in ('right', 'left'):
            nx = cx + (2 if facing == 'right' else -2)
            S.text(p, X(nx), Y(cy + 39.5), str(ch), 11, bold=True, color=QtGui.QColor(232, 232, 232),
                   align='center')
        else:
            dx, dy = (35.5, -0.5) if size != 'sub' else (45.5, -5.0)
            S.text(p, X(cx + dx), Y(cy + dy), str(ch), 11, bold=True, color=QtGui.QColor(232, 232, 232))

    def paint(self, p):
        self._hits = []
        if not self.mapped_only:
            self._cabin(p)
        else:
            self._cabin(p)
        for obj, name, size, facing, centre in data.SLOTS:
            mapped = obj in self.channels
            if self.mapped_only and not mapped:
                continue
            self._icon(p, obj, size, facing, centre, mapped)
            if mapped:
                self._number(p, size, facing, centre, self.channels[obj])

    def click(self, e):
        pos = QtCore.QPointF(e.position()) if hasattr(e, 'position') else QtCore.QPointF(e.pos())
        for obj, r in self._hits:
            if r.contains(pos):
                self.selected = obj
                if self.on_select:
                    self.on_select(obj)
                return True
        return False


# --- EQ editor ---------------------------------------------------------------
GRAPH = (199.0, 655.0, 1913.0, 1138.0)      # outer frame, reference px
DB_TOP, DB_BOT = R.GRAPH_DB_MAX, R.GRAPH_DB_MIN      # -27..+9 dB axis (UI_MAINWINDOW §D.3)


def fx(f):
    """Frequency -> reference x (log axis, 20 Hz at the frame's left edge)."""
    return 200.0 + 555.0 * math.log10(f / 20.0)


def dby(db):
    return 656.0 + (DB_TOP - db) * (1138.0 - 656.0) / (DB_TOP - DB_BOT)


class EqEditor(Item):
    """EqualizerEditorForm: gain fader + spin, the response graph with draggable
    PEQ points (click on empty graph = add a point, as the official editor), the
    FREQ/MAG/Q spins for the selected point, and the delete / reset buttons.

    `on_eq(action, **kw)` is the one callback: 'gain' (db), 'add' (freq, gain),
    'move' (point, freq, gain), 'edit' (point, field, value), 'delete' (point),
    'reset'. The screen applies it to the model and pushes."""
    POINT_R = 9.0            # reference px, the editor's point handles

    def __init__(self, title='MASTER EQ', gain_db=0.0, qlr_on=False, watermark=None, points=None,
                 on_eq=None, selected=None, gain_enabled=True, read_only=False):
        super().__init__(S.rect(40, 612, 1920, 1220))
        self.gain_enabled = gain_enabled          # FUN_140080d10: off while muted / bypassed
        self.read_only = read_only                # FUN_1400597a0: (flags & 0x10001) == 0x10000
        self.title, self.gain_db, self.qlr_on, self.watermark = title, gain_db, qlr_on, watermark
        self.points = list(points or [])          # model.EqPoint objects (shared with the model)
        self.on_eq = on_eq
        self.selected = selected if selected is not None and selected < len(self.points) else None
        self._drag_pt = None
        # Live output-level meter alongside the gain fader (None = no meter shown).
        # Set from engine.meter_levels_changed; reads only, purely a display overlay.
        self.level_frac = None
        self.peak_frac = None
        r = lambda *a: S.rect(*a)
        g = '%.1f dB' % gain_db
        has = self.selected is not None
        sp = self.points[self.selected] if has else None
        self.spins = [
            Spin(r(127, 1173, 261, 1210), g, gain_enabled, tooltip='Gain in decibels of the current equalizer', rule=R.GAIN, number=gain_db),
            Spin(r(335, 1173, 505, 1210), '0.00 Hz', has, rule=R.EQ_FREQ, number=sp.freq if has else 0.0),
            Spin(r(577, 1173, 716, 1210), '0.00 dB', has, rule=R.EQ_MAG, number=sp.gain if has else 0.0),
            Spin(r(832, 1173, 935, 1210), '0.05', has, rule=R.EQ_Q, number=sp.q1 if has else R.EQ_Q.lo),
            Spin(r(952, 1173, 1053, 1210), '0.05', has and qlr_on, rule=R.EQ_Q, number=sp.q2 if has else R.EQ_Q.lo),
        ]
        self.spins[0].bind(lambda v: self._emit('gain', db=v))
        for spin, field in zip(self.spins[1:], ('freq', 'gain', 'q1', 'q2')):
            spin.bind(lambda v, f=field: self._emit('edit', point=self.points[self.selected], field=f, value=v))
        self.children = self.spins

    def _emit(self, action, **kw):
        if self.on_eq is not None:
            self.on_eq(action, **kw)

    # --- geometry helpers (reference px <-> value)
    @staticmethod
    def _freq_at(xref):
        return 20.0 * 10 ** ((xref - 200.0) / 555.0)

    @staticmethod
    def _db_at(yref):
        return DB_TOP - (yref - 656.0) * (DB_TOP - DB_BOT) / (1138.0 - 656.0)

    def _point_rect(self, pt):
        cx, cy = X(fx(max(20.0, pt.freq))), Y(dby(self.gain_db + pt.gain))
        d = D(self.POINT_R * 2)
        return QtCore.QRectF(cx - d / 2, cy - d / 2, d, d)

    def _graph_rect(self):
        x0, y0, x1, y1 = GRAPH
        return S.rect(x0 + 3, y0 + 3, x1 - 3, y1 - 3)

    def _fader_rect(self):
        return S.rect(60, 640, 112, 1153)

    def _ref(self, pos):
        """widget pos -> reference px (inverse of skin.X / skin.Y)."""
        return (pos.x() * S.REF + S.STAGE_ORIGIN[0], pos.y() * S.REF + S.STAGE_ORIGIN[1])

    # --- mouse
    def click(self, e):
        pos = self.pos_of(e)
        if self.tool_click(pos):
            return True
        for i, pt in enumerate(self.points):
            if self._point_rect(pt).contains(pos):
                self.selected, self._drag_pt = i, pt
                self._refresh_spins()
                return True
        if self._fader_rect().contains(pos):
            if not self.gain_enabled:
                return True
            self._drag_pt = 'gain'
            self.drag(pos)
            return True
        if self._graph_rect().contains(pos) and not self.read_only:
            xr, yr = self._ref(pos)
            f = R.quantise(R.EQ_FREQ, self._freq_at(xr))
            g = R.quantise(R.EQ_MAG, self._db_at(yr) - self.gain_db)
            self._emit('add', freq=max(20.0, f), gain=max(R.EQ_MAG.lo, min(R.EQ_MAG.hi, g)))
            return True
        return False

    def drag(self, pos):
        if self._drag_pt is None:
            return
        xr, yr = self._ref(pos)
        if self._drag_pt == 'gain':
            self.gain_db = R.quantise(R.GAIN, max(R.GAIN.lo, min(R.GAIN.hi, self._db_at(yr))))
            self.spins[0].set_number(self.gain_db)
            return
        pt = self._drag_pt
        pt.freq = max(20.0, min(R.EQ_FREQ.hi, R.quantise(R.EQ_FREQ, self._freq_at(xr))))
        pt.gain = max(R.EQ_MAG.lo, min(R.EQ_MAG.hi, R.quantise(R.EQ_MAG, self._db_at(yr) - self.gain_db)))
        self._refresh_spins()

    def release(self, pos):
        d, self._drag_pt = self._drag_pt, None
        if d == 'gain':
            self._emit('gain', db=self.gain_db)
        elif d is not None:
            self._emit('move', point=d, freq=d.freq, gain=d.gain)

    def _refresh_spins(self):
        if self.selected is None or self.selected >= len(self.points):
            return
        pt = self.points[self.selected]
        for spin, v in zip(self.spins[1:], (pt.freq, pt.gain, pt.q1, pt.q2)):
            spin.active = True
            spin.set_number(v)
        self.spins[4].active = self.qlr_on

    def tool_click(self, pos):
        """Delete / reset / copy buttons at the right of the bottom row."""
        cy = Y(1191)
        for cxr, action in ((1789, 'delete'), (1837, 'reset'), (1886, 'copy')):
            if QtCore.QRectF(X(cxr) - D(13), cy - D(13), D(26), D(26)).contains(pos):
                if action == 'delete' and self.selected is not None and self.selected < len(self.points):
                    self._emit('delete', point=self.points[self.selected])
                elif action == 'reset':
                    self._emit('reset')
                elif action == 'copy':
                    self._emit('copy')
                return True
        return False

    def _graph(self, p):
        x0, y0, x1, y1 = GRAPH
        outer = S.rect(x0, y0, x1, y1)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(10, 10, 10, 160))
        p.drawRoundedRect(outer.translated(D(2), D(3)), D(11), D(11))
        p.setBrush(QtGui.QColor(192, 192, 192))
        p.drawRoundedRect(outer, D(11), D(11))
        inner = outer.adjusted(D(3), D(3), -D(3), -D(3))
        p.setBrush(QtGui.QColor(34, 34, 34))
        p.drawRoundedRect(inner, D(8), D(8))
        # inner shadow along the top/left edges
        for i, a in enumerate((150, 90, 45, 20)):
            p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, a), D(1)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(inner.adjusted(D(i), D(i), -D(i), -D(i)), D(8), D(8))
        p.setPen(Qt.NoPen)
        p.save()
        clip = QtGui.QPainterPath(); clip.addRoundedRect(inner, D(8), D(8)); p.setClipPath(clip)
        top, bot = Y(y0 + 3), Y(y1 - 3)
        solid = {50, 100, 500, 1000, 5000, 10000}
        freqs = [f * m for m in (10, 100, 1000) for f in range(2, 10)] + [10000, 20000]
        for f in sorted(set(freqs)):
            if f < 30 or f > 20000:
                continue
            xx = X(fx(f))
            if f in solid:
                p.setPen(QtGui.QPen(QtGui.QColor(150, 150, 152), D(1.5)))
            else:
                pen = QtGui.QPen(QtGui.QColor(150, 150, 150), D(1.2)); pen.setDashPattern([2, 2])
                p.setPen(pen)
            p.drawLine(QtCore.QPointF(xx, top), QtCore.QPointF(xx, bot))
        for db in range(6, -25, -3):
            yy = Y(dby(db))
            pen = QtGui.QPen(QtGui.QColor(165, 165, 165), D(1.2)); pen.setDashPattern([2, 2])
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(X(x0 + 3), yy), QtCore.QPointF(X(x1 - 3), yy))
        p.restore()
        # response curve: the gain plus every PEQ point's response (flat when the
        # list is empty, as on the reference unit)
        p.setPen(QtGui.QPen(S.EQ_ORANGE, D(5), Qt.SolidLine, Qt.FlatCap))
        if not self.points:
            yy = Y(dby(self.gain_db))
            p.drawLine(QtCore.QPointF(X(197), yy), QtCore.QPointF(X(1912), yy))
        else:
            path = QtGui.QPainterPath()
            for i in range(0, 1716, 6):
                xr = 197.0 + i
                f = self._freq_at(xr)
                db = self.gain_db + sum(pt.response_db(f) for pt in self.points)
                db = max(DB_BOT, min(DB_TOP, db))
                pt_ = QtCore.QPointF(X(xr), Y(dby(db)))
                path.moveTo(pt_) if i == 0 else path.lineTo(pt_)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        for i, pt in enumerate(self.points):
            r = self._point_rect(pt)
            p.setPen(QtGui.QPen(QtGui.QColor(20, 20, 20), D(1.5)))
            p.setBrush(QtGui.QColor(209, 35, 35) if i == self.selected else QtGui.QColor(236, 236, 236))
            p.drawEllipse(r)

    def _labels(self, p):
        S.text(p, X(202), Y(630), self.title, 13, bold=True, color=QtGui.QColor(222, 222, 222))
        for f in R.GRAPH_F_GRID:          # 50,100,500,1k,5k,10k,20k (FUN_1400587d0)
            S.text(p, X(fx(f) - 2), Y(631), R.freq_label(f), 11, color=QtGui.QColor(196, 196, 196), align='center')
        db = DB_TOP
        while db >= DB_BOT:               # +9 .. -27 every 3 dB (FUN_140058aa0)
            S.text(p, X(177), Y(dby(db) - 4), R.gain_label(db), 11, color=QtGui.QColor(204, 204, 204),
                   align='right')
            db -= R.GRAPH_DB_GRID_STEP

    def _gain_slider(self, p):
        track = S.rect(81, 645, 91, 1148)
        g = QtGui.QLinearGradient(track.topLeft(), track.topRight())
        for t, c in ((0, 145), (0.5, 210), (1, 150)):
            g.setColorAt(t, QtGui.QColor(c, c, c))
        p.setPen(Qt.NoPen); p.setBrush(QtGui.QBrush(g))
        p.drawRoundedRect(track, track.width() / 2, track.width() / 2)
        self._level_meter(p)
        S.ball(p, X(86), Y(dby(self.gain_db)), D(24), red=True)

    def _level_meter(self, p):
        """Shaded live output-level bar just left of the gain fader. Green→amber→
        red as it approaches full scale, with a held-peak tick. Drawn only when a
        reading is present; otherwise the fader looks exactly as before."""
        if self.level_frac is None:
            return
        bar = S.rect(70, 645, 78, 1148)           # thin bar beside the track
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(18, 18, 18))
        p.drawRoundedRect(bar, bar.width() / 2, bar.width() / 2)
        frac = max(0.0, min(1.0, self.level_frac))
        top = bar.bottom() - bar.height() * frac
        fill = QtCore.QRectF(bar.left(), top, bar.width(), bar.bottom() - top)
        col = QtGui.QColor(70, 200, 90) if frac < 0.8 else (
            QtGui.QColor(225, 180, 45) if frac < 0.95 else QtGui.QColor(225, 45, 45))
        p.setBrush(col)
        p.drawRoundedRect(fill, bar.width() / 2, bar.width() / 2)
        if self.peak_frac is not None:
            pf = max(0.0, min(1.0, self.peak_frac))
            py = bar.bottom() - bar.height() * pf
            p.setPen(QtGui.QPen(QtGui.QColor(245, 245, 245), D(1.5)))
            p.drawLine(QtCore.QPointF(bar.left(), py), QtCore.QPointF(bar.right(), py))

    def _bottom_row(self, p):
        cy = Y(1191)
        S.text(p, X(68), cy, 'GAIN', 12)
        S.text(p, X(277), cy, 'FREQ', 12)
        S.text(p, X(522), cy, 'MAG', 12)
        S.text(p, X(734), cy, 'Q-FACTOR', 12)
        S.text(p, X(1081), cy, 'Q-L/R', 12, color=S.TEXT_DIM)
        for s in self.spins:
            p.save(); s.paint(p); p.restore()
        S.led(p, X(1157), cy, self.qlr_on)
        if self.watermark is not None:
            S.text(p, X(1193), cy, 'WATERMARK', 12, color=S.TEXT_DIM)
            S.led(p, X(1326), cy, self.watermark)
        # tool button group (delete / reset / copy)
        box = S.rect(1762, 1170, 1913, 1213)
        p.setPen(Qt.NoPen); p.setBrush(QtGui.QColor(56, 56, 56, 230))
        p.drawRoundedRect(box, D(5), D(5))
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        for cxr, name in ((1789, 'icon_delete_disabled_16x16.png'), (1837, 'icon_reset_disabled_16x16.png'),
                          (1886, 'icon_copy_16x16.png')):
            pm = S.pix(name)
            p.drawPixmap(QtCore.QRectF(X(cxr) - D(13), cy - D(13), D(26), D(26)), pm, QtCore.QRectF(pm.rect()))

    def paint(self, p):
        self._labels(p)
        self._gain_slider(p)
        self._graph(p)
        self._bottom_row(p)
