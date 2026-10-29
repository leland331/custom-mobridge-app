"""The official tabs (connected state) and the Device screen, laid out at the
reference-pixel positions measured from the reference video. See skin.py for units.

Every screen renders FROM the DspModel (engine.root: the device's tune after a
connect, the reference device from data.py offline) and every control edits that
model and calls engine.push(), which flushes the dirty bits to the device in the
official write order (dspwrite.flush). No static values, no read-only controls.
"""
from __future__ import annotations
import os

from ..qt import QtCore, QtGui, QtWidgets, Qt
from . import skin as S
from . import data
from ... import uirules as UR
from ... import model as M
from ... import scripting
from ... import rearchimes as RC
from .canvas import Canvas, Label, Spin, Combo, Led, Switch, Pill, HSlider, Item
from .components import SpeakerSelector, EqEditor

X, Y, D, R = S.X, S.Y, S.D, S.rect

FILTER_TYPES = ['None', 'High Pass', 'Low Pass', 'Band Pass']          # filterTypeComboBox order
FILTER_TYPE_IDS = [M.FT_NONE, M.FT_HP, M.FT_LP, M.FT_BAND]
FILTER_MODELS = UR.FILTER_MODELS[:4]                                      # mode-1 designs 0..3 (exe spelling)
UPMIX_ITEMS = ['Summing', 'LMS', 'PSD']                                  # centre-channel combo (video)


class _ModelScreen(Canvas):
    """Base for every official screen: holds the model, rebuilds on change,
    pushes edits through the engine."""

    def __init__(self, parent=None, engine=None):
        super().__init__(parent)
        self.engine = engine
        self.root = engine.root if engine is not None else data.demo_model()
        if engine is not None:
            engine.model_changed.connect(self._on_model)

    def _on_model(self, root):
        self.root = root
        self.rebuild()

    @property
    def preset(self) -> M.Preset:
        return self.root.preset()

    def push(self, what: str):
        """Flush the model's pending bits to the device (official: live apply)."""
        if self.engine is not None:
            self.engine.push(what)          # emits model_changed -> rebuild
        else:
            self.rebuild()

    # --- speaker helpers
    def channels(self) -> dict:
        """objectName -> displayed channel number for the mapped speakers."""
        out = {}
        for ch, role in self.root.channel_role.items():
            obj = data.obj_for(role)
            if obj:
                out[obj] = ch + 1
        return out

    def mapped_sorted(self) -> list:
        """[(obj, role, ch)] of the mapped speakers in channel order."""
        rows = []
        for ch, role in self.root.channel_role.items():
            obj = data.obj_for(role)
            if obj:
                rows.append((obj, role, ch))
        return sorted(rows, key=lambda t: t[2])

    def speaker(self, obj) -> M.SpeakerModel | None:
        role = data.role_for(obj)
        return self.root.speaker(role) if self.root.channel_for_role(role) >= 0 else None

    def channel(self, obj) -> int | None:
        ch = self.root.channel_for_role(data.role_for(obj))
        return ch if ch >= 0 else None

    def rebuild(self):
        self.items = []
        self.build()
        self.update()

    def build(self):
        pass

    # --- EQ editor callback shared by master and channel EQ
    def eq_handler(self, owner, what):
        """owner: MasterModel or SpeakerModel (has .eq, .set_gain, .on_eq_changed)."""
        def on_eq(action, **kw):
            if action == 'gain':
                owner.set_gain(kw['db'])
            elif action == 'add':
                owner.eq.add(kw['freq'], kw['gain'], fw=self.root.fw or 210)
            elif action == 'move':
                pt = kw['point']; pt.freq, pt.gain = float(kw['freq']), float(kw['gain'])
                owner.on_eq_changed()
            elif action == 'edit':
                setattr(kw['point'], kw['field'], float(kw['value']))
                owner.on_eq_changed()
            elif action == 'delete':
                owner.eq.remove(kw['point'])
            elif action == 'reset':
                owner.eq.clear()
            elif action == 'copy':
                if not self._copy_menu(owner):
                    return
            self.push(what)
        return on_eq

    def _copy_menu(self, owner) -> bool:
        """"Copy EQ to ..." (FUN_14006a4a0 / FUN_140069870): Copy To All, Master
        (from a channel), one entry per speaker type; copies the point list in the
        model, so the targets go out as ordinary EQ writes."""
        import copy as _copy
        p = self.root.current_preset
        targets = []
        menu = QtWidgets.QMenu('Copy EQ to ...')
        a_all = menu.addAction('Copy To All')
        master = self.root.preset(p).master
        a_master = menu.addAction('Master') if owner is not master else None
        menu.addSeparator()
        per = {}
        for bit, name, _d, _o in M.SPEAKER_TYPES:
            spk = self.root.speaker(bit, p)
            a = menu.addAction(name)
            a.setEnabled(spk is not None and spk is not owner)
            per[a] = spk
        chosen = menu.exec(QtGui.QCursor.pos()) if not getattr(self, '_auto_copy', None) else self._auto_copy(menu)
        if chosen is None:
            return False
        if chosen is a_all:
            targets = [s for s in self.root.speakers_in_preset(p) if s is not owner]
        elif chosen is a_master:
            targets = [master]
        elif per.get(chosen) is not None:
            targets = [per[chosen]]
        for t in targets:
            t.eq.points = [_copy.copy(pt) for pt in owner.eq.points]
            t.on_eq_changed()
        return bool(targets)

    @property
    def rear_chimes(self) -> bool:
        """Firmware with the Rear Chimes input is connected (engine decides)."""
        return bool(getattr(self.engine, 'rear_chimes', False))


class _SpeakerScreen(_ModelScreen):
    """Common top-left: selector + selected speaker's name + MUTE."""
    mapped_only = False

    def __init__(self, parent=None, selected='rightsubwoofer', engine=None):
        super().__init__(parent, engine)
        self.selected = selected
        self.eq_selected = None          # selected PEQ point index, kept across rebuilds
        self.selector = SpeakerSelector(selected, self.mapped_only, self._select, self.channels())
        self.rebuild()
        # Live output-level meter on the selected channel's gain fader (reads only).
        if engine is not None:
            engine.meter_levels_changed.connect(self._on_levels)
            self._register_meter_channel()

    def _register_meter_channel(self):
        ch = self.channel(self.selected)
        if self.engine is not None and ch is not None:
            self.engine.set_meter_channel(ch + 1)

    def _on_levels(self, levels):
        eq = getattr(self, 'eq', None)
        ch = self.channel(self.selected)
        if eq is None or ch is None:
            return
        reading = levels.get(ch + 1)
        eq.level_frac = None if reading is None else reading.fraction()
        eq.peak_frac = None if reading is None else reading.peak_fraction()
        self.update()

    def _select(self, obj):
        self.selected = obj
        self.eq_selected = None
        self._register_meter_channel()
        self.rebuild()

    def rebuild(self):
        self.selector.channels = self.channels()
        self.selector.selected = self.selected
        self.items = [self.selector]
        self.build()
        self.update()

    def _eq(self, title, owner, qlr_on, watermark=None, what='eq'):
        def on_eq(action, **kw):
            if action == 'add':
                self.eq_selected = len(owner.eq.points)
            elif action == 'delete':
                self.eq_selected = None
            self.eq_handler(owner, what)(action, **kw)
        flags = _product_flags(self.root)
        gain_on = not (getattr(owner, 'mute', False) or getattr(owner, 'passthrough', False))
        ro = (flags & 0x10001) == 0x10000
        if owner is self.root.preset().master and not flags & 0x2:     # master-EQ editor needs flag bit 1
            gain_on, ro = False, True
        ed = EqEditor(title, owner.gain_db, qlr_on=qlr_on, watermark=watermark,
                      points=owner.eq.points, on_eq=on_eq, selected=self.eq_selected,
                      gain_enabled=gain_on, read_only=ro)
        return self.add(ed)

    def _header(self):
        name = data.slot(self.selected)[1]
        s = self.speaker(self.selected)
        self.add(Label(X(650), Y(276.5), name, 22, color=QtGui.QColor(236, 236, 236)))
        self.add(Label(X(1090), Y(275), 'MUTE', 11,
                       color=QtGui.QColor(222, 222, 222) if s else S.TEXT_DIM))
        self.add(Switch(R(1141, 262, 1197, 288), bool(s and s.mute), enabled=s is not None)).bind(
            lambda on: (s.set_mute(on), self.push('mute')))


class ConfigMasterEqScreen(_SpeakerScreen):
    title = 'CONFIG & MASTER EQ'

    def build(self):
        root = self.root
        obj = self.selected
        role = data.role_for(obj)
        s = self.speaker(obj)
        ch = self.channel(obj)
        self._header()
        self.add(Label(X(650), Y(341), 'Output Mapping', 12))
        current = data.OUTPUTS[ch + 1] if ch is not None and ch + 1 < len(data.OUTPUTS) else 'No Mapping'

        def on_map(idx):
            if idx == 0:
                if ch is not None:
                    root.unmap_channel(ch)
            else:
                root.map_speaker(role, idx - 1)
            self.push('output mapping')
        self.add(Combo(R(868, 323, 1203, 360), data.OUTPUTS, current)).bind(on_map)
        rear = self.rear_chimes
        upmix_dy = 0.0
        if s is not None:
            names = ['Left', 'Right'] if obj in data.SUBS else data.INPUTS + ([RC.NAME] if rear else [])
            bits = dict(M.INPUT_BITS, **{RC.NAME: RC.BIT})
            mask = root.input_map.get(ch, 0)
            # connected, the dots edit the map read by 0x0711 on this connection; without
            # that read a click would send unread outputs as 0 (whole-map 0x0701)
            can_map = self.engine is None or not self.engine.connected or root.routing_read

            def on_input(_on, bit):
                if not can_map:
                    return
                # current mask at click time: a mask captured at build time drops other dots
                # clicked since (LB-03 rollback, docs/chime-routing/LB-RC1.md)
                root.set_input_map(ch, root.input_map.get(ch, 0) ^ (1 << bit))
                self.push('input mapping')
            if len(names) == 2:
                self.add(Label(X(650), Y(392), 'Input Mapping', 12))
                for i, n in enumerate(names):
                    b = bits[n]
                    self.add(Led(X(880 + 177 * i), Y(392), bool(mask >> b & 1), n, X(909 + 177 * i),
                                 enabled=can_map)).bind(
                        lambda on, b=b: on_input(on, b))
            else:
                self.add(Label(X(650), Y(425), 'Input Mapping', 12))
                # Rear Chimes (LB-03+) takes a 4th row; the Upmix row moves down one
                upmix_dy = 32.5 * max(0, (len(names) + 1) // 2 - 3)
                for i, n in enumerate(names):
                    col, row = i % 2, i // 2
                    b = bits[n]
                    self.add(Led(X(880 + 177 * col), Y(392 + 32.5 * row), bool(mask >> b & 1), n,
                                 X(909 + 177 * col), enabled=can_map)).bind(lambda on, b=b: on_input(on, b))
            if obj in data.CENTERS:
                up = self.preset.upmix
                self.add(Label(X(650), Y(508 + upmix_dy), 'Upmix Algorithm', 12))
                self.add(Combo(R(868, 490 + upmix_dy, 1203, 527 + upmix_dy), UPMIX_ITEMS,
                               UPMIX_ITEMS[min(up.algorithm, len(UPMIX_ITEMS) - 1)])).bind(
                    lambda idx: (up.set_algorithm(idx), self.push('upmix')))
        # Digital Inputs: the InputGains form exists only with product flag bit 3 (FUN_1400434b0)
        if _product_flags(root) & 0x8:
            p = root.current_preset
            self.add(Label(X(1285), Y(271), 'Digital Inputs', 22, color=QtGui.QColor(236, 236, 236)))
            # Rear Chimes (LB-03+): level only (entry k = 6); its volume is fixed in firmware
            gain_k = dict(M.INPUT_GAIN_INDEX, **{RC.NAME: RC.GAIN_K})
            for (lx, sx), names in (((1302, 1393), ('Stereo', 'Beeps', 'Voice')),
                                    ((1556, 1647), ('Prompt', 'Phone') + ((RC.NAME,) if rear else ()))):
                for i, n in enumerate(names):
                    cy = (363, 410, 456)[i]
                    k = gain_k[n]
                    g = root.input_gains[p][k]
                    self.add(Label(X(lx), Y(cy), n, 12))
                    self.add(Spin(R(sx, cy - 18.5, sx + 134, cy + 18.5), '%.1f dB' % g, True, rule=UR.INPUT_GAIN, number=g)).bind(
                        lambda v, k=k: (root.set_input_gain(k, v), self.push('input gain')))
            ms = root.input_delay_ms[p]
            self.add(HSlider(X(1302), X(1622), Y(557), min(1.0, ms / M.INPUT_DELAY_MAX_MS))).bind(
                lambda frac: (root.set_input_delay(UR.quantise(UR.INPUT_DELAY, frac * M.INPUT_DELAY_MAX_MS)),
                              self.push('mono delay')))
            self.add(Spin(R(1642, 539, 1776, 576), '%.1f ms' % ms, True, rule=UR.INPUT_DELAY, number=ms)).bind(lambda v: (root.set_input_delay(v), self.push('mono delay')))
        self.eq = self._eq('MASTER EQ', self.preset.master, qlr_on=False, what='master eq')

    def paint_static(self, p):
        S.vsep(p, X(610), Y(248), Y(592))
        S.vsep(p, X(1245), Y(248), Y(592))
        if _product_flags(self.root) & 0x8:
            S.group(p, R(1287, 327, 1792, 488), 'Gains')
            S.group(p, R(1287, 525, 1792, 590), 'Mono Delays')


class ChannelEqScreen(_SpeakerScreen):
    title = 'CHANNEL EQ'
    mapped_only = True

    def build(self):
        s = self.speaker(self.selected)
        if s is None:
            # the CHANNEL EQ selector only shows mapped speakers; fall back to the first
            rows = self.mapped_sorted()
            if not rows:
                return
            self.selected = rows[0][0]
            self.selector.selected = self.selected
            s = self.speaker(self.selected)
        self._header()
        t = s.filter_type
        xo = UR.crossover_editor_enabled(_product_flags(self.root), s.role, 0)   # line 11768
        lp_on = xo and t in (M.FT_LP, M.FT_BAND)
        hp_on = xo and t in (M.FT_HP, M.FT_BAND)
        self.add(Label(X(650), Y(341), 'Filter Type', 12))
        self.add(Combo(R(868, 323, 1166, 360), FILTER_TYPES, FILTER_TYPES[FILTER_TYPE_IDS.index(t)])).bind(
            lambda idx: (s.set_filter_type(FILTER_TYPE_IDS[idx]), self.push('filter type')))
        self.add(Label(X(1250), Y(341), 'Filter Model', 12))
        if s.mode == M.MODE_BIQUAD:
            self.add(Combo(R(1467, 323, 1765, 360), ['Biquad'], 'Biquad', active=t != M.FT_NONE,
                           tooltip=UR.BIQUAD_MODEL_TOOLTIP))
        else:
            cur = FILTER_MODELS[min(s.filter_design, len(FILTER_MODELS) - 1)]
            self.add(Combo(R(1467, 323, 1765, 360), FILTER_MODELS, cur, active=t != M.FT_NONE)).bind(
                lambda idx: (s.set_design(idx), self.push('filter model')))
        self.add(Label(X(650), Y(403), 'Lo-P Hz', 12))
        self.add(Spin(R(868, 385, 1166, 422), '%d Hz' % s.f1, lp_on, 'center', rule=UR.CUTOFF, number=s.f1)).bind(
            lambda v: (s.set_lop(int(v)), self.push('crossover')))
        self.add(Label(X(1250), Y(403), 'Hi-P Hz', 12))
        self.add(Spin(R(1467, 385, 1765, 422), '%d Hz' % s.f2, hp_on, 'center', rule=UR.CUTOFF, number=s.f2)).bind(
            lambda v: (s.set_hip(int(v)), self.push('crossover')))
        slope_rule = UR.SLOPE_BIQUAD if s.mode == M.MODE_BIQUAD else UR.SLOPE
        slope_tip = UR.BIQUAD_SLOPE_TOOLTIP if s.mode == M.MODE_BIQUAD else None
        self.add(Label(X(650), Y(464), 'Lo-P Slope', 12))
        self.add(Spin(R(868, 446, 1166, 483), '%d dB/8ve' % s.slope1, lp_on, 'center', rule=slope_rule, number=s.slope1,
                          tooltip=slope_tip)).bind(
            lambda v: (s.set_slope1(int(v)), self.push('crossover')))
        self.add(Label(X(1250), Y(464), 'Hi-P Slope', 12))
        self.add(Spin(R(1467, 446, 1765, 483), '%d dB/8ve' % s.slope2, hp_on, 'center', rule=slope_rule, number=s.slope2,
                          tooltip=slope_tip)).bind(
            lambda v: (s.set_slope2(int(v)), self.push('crossover')))
        self.add(Led(X(664), Y(520), s.invert_phase, 'Invert Phase', X(692),
                     enabled=UR.invert_phase_enabled(_product_flags(self.root)))).bind(
            lambda on: (s.set_invert_phase(on), self.push('invert phase')))
        partner_possible = any(s.role in pr and (pr[0] if s.role == pr[1] else pr[1]) in self.root.role_channel
                               for pr in M.DspModel.XOVER_PAIRS)

        def on_link(on):
            if on:
                self.root.link_xover_partner(s)
            else:
                s.link_xover(None)
            self.push('xover link')
        self.add(Led(X(880), Y(520), s.xover_partner is not None, 'XOVER L/R Link', X(908),
                     enabled=partner_possible)).bind(on_link)
        self.add(Led(X(1264), Y(520), s.passthrough, 'Direct Passthrough', X(1292))).bind(
            lambda on: (s.set_passthrough(on), self.push('passthrough')))
        self.eq = self._eq('CHANNEL EQ', s, qlr_on=True, watermark=False, what='channel eq')

    def paint_static(self, p):
        S.vsep(p, X(610), Y(248), Y(592))


def _intro(p, icon, lines):
    pm = S.pix(icon)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    p.drawPixmap(R(80, 278, 131, 329), pm, QtCore.QRectF(pm.rect()))
    for x, cy, parts in lines:
        S.runs(p, X(x), Y(cy), parts, 12, color=QtGui.QColor(226, 226, 226))


class _Screen(_ModelScreen):
    """Non-speaker screens: build() fills self.items from the model."""

    def __init__(self, parent=None, engine=None):
        super().__init__(parent, engine)
        self.rebuild()


class TimeAlignScreen(_Screen):
    title = 'TIME ALIGN'
    LINES = [
        (148, 288, [('Time alignment adjusts the delays on each', 'b')]),
        (148, 319, [('speaker according to a chosen listening point.', 'b')]),
        (80, 369, [('To correctly setup time alignment in your vehicle:', '')]),
        (80, 435, [('1. Choose the ', ''), ('listening point', 'b'), ('.', '')]),
        (99, 466, [('This is the ', ''), ('audio focal point', 'i'), (', and is usually where the', '')]),
        (99, 497, [("driver's headrest is located.", '')]),
        (80, 560, [('2. Measure the ', ''), ('speaker distances', 'b'), ('.', '')]),
        (99, 591, [('This is the distance from each speaker in a direct line to', '')]),
        (99, 622, [('the ', ''), ('listening point', 'i'), ('.', '')]),
        (80, 685, [('3. Calculate the ', ''), ('time delays.', 'b')]),
        (99, 716, [('Click the ', ''), ('Calc. Time Delays', 'i'), (" button to set each channel's", '')]),
        (99, 747, [('time delay', 'i'), (' to achieve the ', ''), ('audio focal point', 'i'), ('.', '')]),
        (80, 810, [('4. Adjust the time delay manually to ', ''), ('fine tune', 'b'), (' (', ''),
                   ('optional', 'i'), (').', '')]),
        (99, 841, [('If required, further adjust the time delays to achieve', '')]),
        (99, 872, [('desired ', ''), ('audio focal point', 'i'), ('.', '')]),
    ]
    manual = False       # "Manually edit calculated delays" (UI state, FUN_1400467?0)

    def build(self):
        ta = self.preset.timealign
        metric = ta.units == 0
        unit = ta.unit_label
        self.add(Label(X(480), Y(1110), 'Metric distances (%s)' % unit, 12, align='right'))
        # manual delay editing disables the unit switch and the Calc button (UI_REGION_1 §2.7)
        self.add(Switch(R(501, 1097, 557, 1123), metric, enabled=not self.manual)).bind(
            lambda on: (ta.set_units(0 if on else 1), self.push('time align units')))
        self.add(Label(X(485), Y(1158), 'Manually edit calculated delays', 12, align='right'))

        def on_manual(on):
            self.manual = on
            self.rebuild()
        self.add(Switch(R(500, 1145, 556, 1171), self.manual)).bind(on_manual)
        for x, t in ((657, 'Channel'), (900, 'Distance'), (1069, 'Calc. Delay')):
            self.add(Label(X(x), Y(279), t, 12, bold=True, color=QtGui.QColor(232, 232, 232)))
        for i, (obj, role, ch) in enumerate(self.mapped_sorted()):
            s = self.root.speaker(role)
            cy = 344 + 46.4 * i
            self.add(Label(X(657), Y(cy), '%d: %s' % (ch + 1, data.slot(obj)[1]), 12))
            self.add(Spin(R(901, cy - 18.5, 1049, cy + 18.5), '%.1f %s' % (s.distance, unit), not self.manual,
                          rule=UR.TA_DISTANCE, number=s.distance, suffix=' ' + unit)).bind(
                lambda v, s=s: (s.set_distance(v), self.push('distance')))
            self.add(Spin(R(1063, cy - 18.5, 1211, cy + 18.5), '%.2f ms' % s.delay_ms, self.manual,
                          rule=UR.TA_DELAY, number=s.delay_ms)).bind(
                lambda v, s=s: (s.set_delay(v), self.push('delay')))
        self.add(Pill(R(657, 1133, 905, 1183), 'RESET')).bind(
            lambda _v: (ta.reset(), self.push('time align reset')))
        self.add(Pill(R(935, 1133, 1182, 1183), 'CALC. TIME DELAYS', enabled=not self.manual)).bind(
            lambda _v: (ta.distances_to_delays(), self.push('calc time delays')))

    def paint_static(self, p):
        _intro(p, 'measure_tape.png', self.LINES)
        S.vsep(p, X(610), Y(248), Y(1205))


class LimiterScreen(_Screen):
    title = 'LIMITER'
    LINES = [
        (148, 288, [('The limiter uses Dynamic Range Compression', 'b')]),
        (148, 319, [('(DRC) to prevent audio distortion caused by', 'b')]),
        (148, 350, [('clipping.', 'b')]),
        (80, 399, [('To correctly setup channel limiting:', '')]),
        (80, 466, [('1. Show the ', ''), ('signal level', 'b'), (' on channel to be limited.', '')]),
        (99, 497, [('To display a ', ''), ('Real-Time Analysis (RTA)', 'b'), (" of a channel's", '')]),
        (99, 528, [('audio signal level, select the channel using the checkbox', '')]),
        (99, 559, [('below ', ''), ('RTA', 'b'), ('.', '')]),
        (80, 622, [('2. Bring the channel close to ', ''), ('clipping', 'b'), (' point.', '')]),
        (99, 653, [('Increase input volume, and when the channel begins to', '')]),
        (99, 684, [('clip, you will hear distortion on that channel.', '')]),
        (80, 747, [('3. Set the ', ''), ('limiter slider', 'b'), (' to the found ', ''), ('clipping', 'b'),
                   (' point.', '')]),
        (99, 778, [('This sets the DRC algorithm to limit at this level (in dBFS,', '')]),
        (99, 809, [('dB relative to full scale) on this channel.', '')]),
    ]
    SPAN = -UR.LIMITER.lo      # slider covers -36..0 dBFS (LIMITER_SLIDER)
    rta_on: set = None         # channels whose RTA checkbox is ticked (FUN_140055830)
    rta_db: dict = None

    def _rta_tick(self):
        if self.engine is None or not self.rta_on:
            return
        for ch in sorted(self.rta_on):
            self.engine.rta_request(ch)

    def _rta_level(self, ch, db):
        if self.rta_on and ch in self.rta_on:
            self.rta_db[ch] = max(UR.LIMITER.lo, min(0.0, db))      # FUN_140055590 clamps to [min, max]
            for it in self.items:
                if isinstance(it, _RtaBar) and it.ch == ch:
                    it.value = self.rta_db[ch]
            self.update()

    def _rta_toggle(self, ch, on):
        if on:
            self.rta_on.add(ch)
        else:
            self.rta_on.discard(ch)
            self.rta_db.pop(ch, None)
        if self.rta_on and not self._rta_timer.isActive():
            self._rta_timer.start(300)
        elif not self.rta_on:
            self._rta_timer.stop()
        self.rebuild()

    def build(self):
        if self.rta_on is None:
            self.rta_on, self.rta_db = set(), {}
            self._rta_timer = QtCore.QTimer(self)
            self._rta_timer.timeout.connect(self._rta_tick)
            if self.engine is not None:
                self.engine.rta_level.connect(self._rta_level)
        for x, t in ((657, 'Channel'), (900, 'Limit'), (1733, 'RTA')):
            self.add(Label(X(x), Y(279), t, 12, bold=True, color=QtGui.QColor(232, 232, 232)))
        for i, (obj, role, ch) in enumerate(self.mapped_sorted()):
            s = self.root.speaker(role)
            cy = 344 + 46.4 * i
            self.add(Label(X(657), Y(cy), '%d: %s' % (ch + 1, data.slot(obj)[1]), 12))
            lim = s.limit_dbfs
            live = UR.limiter_controls_enabled(s.passthrough)        # FUN_1400558d0
            self.add(Spin(R(901, cy - 18.5, 1049, cy + 18.5), '%.1f dBFS' % lim, live, rule=UR.LIMITER, number=lim,
                          tooltip='')).bind(
                lambda v, s=s: (s.set_limit(v), self.push('limiter')))
            self.add(HSlider(X(1083), X(1683), Y(cy + 5), max(0.0, min(1.0, 1.0 + lim / self.SPAN)),
                             enabled=live)).bind(
                lambda frac, s=s: (s.set_limit(UR.quantise(UR.LIMITER, (frac - 1.0) * self.SPAN)), self.push('limiter')))
            if ch in self.rta_on:
                self.add(_RtaBar(R(1083, cy - 3, 1683, cy + 3), ch, self.rta_db.get(ch, UR.LIMITER.lo)))
            self.add(Led(X(1718), Y(cy + 2), ch in self.rta_on, tooltip='', enabled=live)).bind(
                lambda on, ch=ch: self._rta_toggle(ch, on))

    def paint_static(self, p):
        _intro(p, 'audio_waves.png', self.LINES)
        S.vsep(p, X(610), Y(248), Y(1205))


class ScriptingScreen(_Screen):
    """ScriptScreen (FUN_14008cd20): Run script ... / Restart / Reset + the Lua host."""
    title = 'SCRIPTING'

    def __init__(self, parent=None, engine=None):
        self.host = None
        self.title_text = scripting.NO_SCRIPT
        self.ran = False
        super().__init__(parent, engine)
        # scriptWidget: the pane scripts build their UI in (widget.rootLayout()), log below
        self.pane = QtWidgets.QWidget(self)
        self.pane.setGeometry(S.irect(R(90, 323, 1898, 1193)))
        self.pane_layout = QtWidgets.QVBoxLayout(self.pane)
        self.pane_layout.setContentsMargins(0, 0, 0, 0)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.console.setStyleSheet('QPlainTextEdit { background: rgb(45,45,45); color: #d1d1d1; }')
        self.script_area = QtWidgets.QVBoxLayout()
        self.pane_layout.addLayout(self.script_area)
        self.pane_layout.addWidget(self.console, 1)
        if engine is not None:
            engine.bus_event.connect(self._on_bus_event)

    def build(self):
        self.add(Pill(R(48, 240, 165, 284), 'Run script ...', True, 11)).bind(lambda _v: self.run_script())
        self.add(Pill(R(186, 240, 303, 284), 'Restart', self.ran, 11)).bind(lambda _v: self.restart())
        self.add(Pill(R(324, 240, 441, 284), 'Reset', self.ran, 11)).bind(lambda _v: self.reset())
        self.add(Label(X(1908), Y(261), self.title_text, 12, align='right'))

    def paint_static(self, p):
        r = R(65, 323, 1898, 1193)
        p.fillRect(r, QtGui.QColor(45, 45, 45))
        S.text(p, X(73), Y(342), '$', 12, color=QtGui.QColor(40, 40, 210), shadow=False)

    # --- the Lua host -----------------------------------------------------------
    @staticmethod
    def dirs():
        app_data = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppLocalDataLocation)
        app_dir = QtCore.QCoreApplication.applicationDirPath() or os.getcwd()
        return scripting.script_dirs(app_data, app_dir)

    def _host(self):
        if self.host is None:
            self.host = scripting.ScriptHost(engine=self.engine, log=self.append,
                                             lib_dirs=[os.path.join(d, 'lib') for d in self.dirs()],
                                             timer_factory=lambda: QtCore.QTimer(self),
                                             pane_layout=self.script_area)
        return self.host

    def append(self, text):
        self.console.appendPlainText(text)

    def _on_bus_event(self, ev):
        if self.host is not None and self.host.lua is not None:
            self.host.deliver(ev)

    def run_script(self, module=None):
        """FUN_14008d7c0: list modules, Script Selector (RUN / CANCEL), log file, run."""
        win = self.window()
        if module is None:
            mods = scripting.find_modules(self.dirs())
            if not mods:
                if getattr(win, '_auto_answer', None) is None:
                    QtWidgets.QMessageBox.warning(self, scripting.NO_MODULES_TITLE,
                                                  scripting.NO_MODULES_TEXT % self.dirs()[0])
                return False
            dlg = QtWidgets.QInputDialog(self)
            dlg.setWindowTitle(scripting.SELECTOR_TITLE)
            dlg.setComboBoxItems([m.selector_text for m in mods])
            dlg.setOkButtonText('RUN')
            dlg.setCancelButtonText('CANCEL')
            dlg.resize(400, 300)
            if not dlg.exec():
                return False
            texts = [m.selector_text for m in mods]
            module = mods[texts.index(dlg.textValue())] if dlg.textValue() in texts else None
            if module is None:
                return False
        if not os.path.isfile(module.script):
            return False
        host = self._host()
        if host.log_file is None and getattr(win, '_auto_answer', None) is None:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, scripting.LOG_FILE_FILTER, '', '*.txt')
            if path:
                host.log_file = path
        self.title_text = os.path.abspath(module.script)
        self.ran = True
        self.rebuild()
        return host.run(module.script)

    def restart(self):
        return self._host().restart() if self.ran else False

    def reset(self):
        if not self.ran:
            return False
        self._host().reset()
        return True


class DeviceScreen(_Screen):
    """Shown when the Connected button is clicked (replaces the tab content)."""
    title = 'DEVICE'

    def build(self):
        from ... import products, parity
        pr = data.PRODUCT
        root = self.root
        pid = getattr(root, 'product', None) if root is not None else None
        known = pid in products.BY_ID
        flags = _product_flags(root)
        ver = getattr(self.engine, 'app_version', None) if self.engine is not None else None
        fw_text = '.'.join('%02d' % int(x) for x in ver.split('.')) if ver else pr['firmware']
        name = products.name(pid) if known else pr['name']
        desc = products.description(pid) if known else pr['subtitle']
        self.add(Label(X(650), Y(281), name, 23, color=QtGui.QColor(236, 236, 236)))
        self.add(Label(X(650), Y(333), desc, 16, color=QtGui.QColor(226, 226, 226)))
        self.add(Label(X(650), Y(438), 'Firmware Version:', 12))
        self.add(Label(X(887), Y(438), fw_text, 12))
        vehicle = getattr(self.engine, 'vehicle_name', None) if self.engine is not None else None
        if not flags >> 9 & 1 and vehicle:           # FUN_14004e2c0: flag bit 9 clear and a vehicle object
            self.add(Label(X(650), Y(483), 'Vehicle Type:', 12))
            self.add(Label(X(887), Y(483), vehicle, 12))
        connected = bool(self.engine is not None and self.engine.connected)
        self.add(Pill(R(651, 889, 896, 938), UR.DEVICE_SCREEN_TEXTS['nvm'], tooltip=''))
        self.add(Pill(R(925, 889, 1171, 938), UR.DEVICE_SCREEN_TEXTS['nvm_fixed']))
        self.add(Pill(R(1200, 889, 1445, 938), UR.DEVICE_SCREEN_TEXTS['nvm_app']))
        # FIRMWARE UPDATE: disabled while firmware sending (0x0201) is held
        # FUN_14004d530: state 5 (Recovery) -> "FIRMWARE RECOVERY", else "FIRMWARE UPDATE"
        recovering = getattr(self.engine, 'conn_state', None) == UR.STATE_RECOVERY
        self.add(Pill(R(651, 978, 896, 1026), UR.DEVICE_SCREEN_TEXTS['recovery' if recovering else 'update'], split=True,
                      enabled=connected and 0x0201 not in parity.LOCKED_COMMANDS,
                      tooltip=UR.DEVICE_SCREEN_TEXTS['tt_update'])).bind(
            lambda _v: self.window().firmware_update() if hasattr(self.window(), 'firmware_update') else None)
        # FACTORY RESET split button: click = factory reset; arrow = Restart (+ Restore
        # with flag bit 0). Reset/FactoryReset (0x0010/0x0011) stay disabled while held.
        reset_open = connected and not ({0x0010, 0x0011} & set(parity.LOCKED_COMMANDS))
        fr = self.add(_SplitPill(R(1626, 978, 1871, 1026), 'FACTORY RESET', split=True, enabled=True,
                                 tooltip=UR.DEVICE_SCREEN_TEXTS['tt_reset']))
        fr.main_enabled = reset_open
        fr.menu_items = [('Restart', UR.DEVICE_SCREEN_TEXTS['tt_restart'], reset_open, self._restart)]
        if flags & 1:
            fr.menu_items.append(('Restore', UR.DEVICE_SCREEN_TEXTS['tt_restore'], True, self._restore))
        fr.bind(lambda _v: self._factory_reset())
        # [CUSTOM] auto-update toggles start OFF so an update never overwrites LB-PAC01
        self.add(Led(X(688), Y(1130), False, 'Check for Updates Automatically', X(718), px=13))
        self.add(Led(X(688), Y(1167), False, 'Download Online Updates Automatically', X(718), px=13))

    def _ask(self, icon, text, buttons):
        win = self.window()
        auto = getattr(win, '_auto_answer', None)
        if auto is not None:
            return auto
        return QtWidgets.QMessageBox(icon, UR.APP_NAME, text, buttons, self).exec()

    def _factory_reset(self):
        """FUN_14004d180: Warning Yes|No, then job 0xc (0x0011); busy -> Unable to reset!"""
        Q = QtWidgets.QMessageBox
        if self._ask(Q.Warning, UR.FACTORY_RESET_PROMPT, Q.Yes | Q.No) == Q.No:
            return False
        if self.engine is None or not self.engine.factory_reset():
            self._ask(Q.NoIcon, UR.FACTORY_RESET_BUSY, Q.Ok)
            return False
        return True

    def _restart(self):
        Q = QtWidgets.QMessageBox
        if self.engine is None or not self.engine.restart():
            self._ask(Q.NoIcon, UR.REBOOT_BUSY, Q.Ok)
            return False
        return True

    def _restore(self):
        """FUN_14004dfb0: the Open Tune flow, starting in the AppData 'backup' folder."""
        win = self.window()
        if hasattr(win, 'restore_backup'):
            return win.restore_backup()
        return False

    def paint_static(self, p):
        img = UR.product_image(getattr(self.root, 'product', None)) if self.root is not None else None
        if img is None and self.root is None:
            img = 'dag2_a2b_ford_pro.png'
        if img is not None:                      # products with no image in the exe draw none
            pm = S.pix(img)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            p.drawPixmap(R(96, 262, 543, 610), pm, QtCore.QRectF(pm.rect()))
        S.vsep(p, X(610), Y(248), Y(1213))
        S.group(p, R(92, 1080, 554, 1207), 'Device Support', 12)
        S.group(p, R(652, 1080, 1871, 1207), 'Firmware Update Options', 12)
        link = QtGui.QColor('#dbdbdb')
        for cy, t in ((1128, 'User Guide'), (1170, 'Install Guide')):
            pm = S.pix('icon_extlink_12.png')
            p.drawPixmap(R(116, cy - 10, 134, cy + 8), pm, QtCore.QRectF(pm.rect()))
            w = S.text(p, X(147), Y(cy), t, 12, color=link)
            p.fillRect(QtCore.QRectF(X(147), Y(cy + 10), w, D(1.5)), link)


class _RtaBar(Item):
    """signalDbfsBar: the RTA level, -36..0 dBFS (x10 in the exe's int bar)."""

    def __init__(self, r, ch, value):
        super().__init__(r)
        self.ch, self.value = ch, value

    def paint(self, p):
        lo = UR.LIMITER.lo
        frac = max(0.0, min(1.0, (self.value - lo) / -lo))
        p.fillRect(self.r, QtGui.QColor(30, 30, 30))
        if frac > 0:
            p.fillRect(QtCore.QRectF(self.r.left(), self.r.top(), self.r.width() * frac, self.r.height()),
                       QtGui.QColor('#D12323'))


class _SplitPill(Pill):
    """QToolButton MenuButtonPopup: the body runs the default action, the arrow
    (right part) opens the menu of extra actions."""
    main_enabled = True
    menu_items: list = []

    def click(self, e):
        arrow = self.pos_of(e).x() > self.r.right() - self.r.width() * 0.18
        if arrow:
            m = QtWidgets.QMenu()
            for text, tip, enabled, fn in self.menu_items:
                a = m.addAction(text)
                a.setToolTip(tip); a.setEnabled(enabled)
                a.triggered.connect(lambda _c=False, f=fn: f())
            m.setToolTipsVisible(True)
            m.exec(QtGui.QCursor.pos())
            return True
        if not self.main_enabled:
            return False
        self.fire(None)
        return True


OFFICIAL_SCREENS = [ConfigMasterEqScreen, ChannelEqScreen, TimeAlignScreen, LimiterScreen,
                    ScriptingScreen]


def _product_flags(root) -> int:
    """The product's flag word (exe product table); 0 for a product the table does
    not know (nothing enabled, like the exe's Incompatible state). The offline
    preview carries its own product (data.demo_model)."""
    from ... import products
    return products.flags(getattr(root, 'product', None))
