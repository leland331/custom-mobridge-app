"""Read the whole tune FROM the device into a DspModel (the official DspRead).

Order follows the capture (EXE_DEEP_RE_OCT22 §8 [CAP]): name, fw version, preset
count, current preset, routing, sourcesel, then master props, master EQ, input
gains, input delay (each over every preset), then per channel the 0x0710 record
and 0x0714 sub 2/3/5 (each over every preset), then 0x070f sub 0..3. Steps whose
request bit the product's flags filter out are skipped (FUN_1400ac190). Builders and reply parsers are cmdspec's (every byte pinned there). Reads
only: nothing here changes device state, and the loaded model ends CLEAN (no
dirty bits), exactly like the official app after connect.

Kept as its own module so the connect path stays modular and easy to roll back
(project rule).
"""
from __future__ import annotations

import math

from . import cmdspec as C
from . import dspwrite as D
from . import products as P
from . import model as M
from .message import MdiMessage

LIMITER_OFF = 0x7FFFFFFF


def limiter_dbfs(raw: int) -> float:
    """i32 limiter -> dBFS (0x7FFFFFFF = 0 dBFS = off); inverse of dspwrite's LIMITER."""
    if raw <= 0 or raw >= LIMITER_OFF:
        return 0.0
    return 20.0 * math.log10(raw / 2147483648.0)


def gain_db(raw: int, fw: int) -> float:
    return (raw - M.gain_unity(fw)) / 512.0


class Reader:
    """Drives the read sweep over an `exchange(cmd, payload) -> reply|None` callable."""

    # the exe's device struct input-gain table (dev+0x258), one per app session (zero at start)
    dev_input_raw: dict = {}

    def __init__(self, exchange, log=None):
        self.exchange = exchange
        self.log = log or (lambda s: None)
        self.sent = 0

    def _ask(self, cmd: int, payload: bytes, parse=None, **kw):
        """Send one read; return the parsed reply (or raw bytes without `parse`),
        None on no reply or a reply that does not parse (logged, sweep goes on)."""
        self.sent += 1
        try:
            reply = self.exchange(cmd, payload)
            if reply is None:
                return None
            return parse(reply.data, **kw) if parse is not None else reply.data
        except Exception as e:          # one bad reply must not kill the sweep
            self.log('read 0x%04x %s: %s' % (cmd, payload.hex(), e))
            return None

    def read_tune(self, root: M.DspModel, product_id: int | None = None, read_mask: int | None = None) -> M.DspModel:
        """The DspRead job: steps in the exe's order, each step looping over the
        presets (step-major, preset-minor), with the request mask
        filter(0xff1f001f) for the product (FUN_1400aeb00 / FUN_1400ac190)."""
        if product_id is not None:
            root.product = product_id
        # FUN_140099030: unknown product -> 3, > 64 channels -> 4; no read either way
        if root.product not in P.BY_ID or not 0 < P.channel_count(root.product) <= 64:
            self.log('product 0x%04x is not supported: nothing read' % (root.product or 0))
            return root
        root.channel_count = P.channel_count(root.product)
        flags = P.flags(root.product)
        # +0x1bf97 (after a firmware update): mask 0x8000000 = 0x0714 sub 0 only
        mask = P.request_filter(P.DSPREAD_MASK if read_mask is None else read_mask, flags)
        # --- AppName, AppVersion
        if mask & D.REQ_NAME:
            n = self._ask(0x0714, C.read_name(), C.parse_name)
            if n:
                root.name = n['name'].split(b'\0', 1)[0].decode('latin-1')
        if mask & D.REQ_FW:
            v = self._ask(0x0714, C.read_fw_version(), C.parse_fw_version)
            if v:
                root.set_fw(v['fw'])
        fw = root.fw
        # --- presets
        presets = 0
        if mask & D.REQ_PRESET:
            pc = self._ask(0x0716, C.read_preset_count(), C.parse_preset_count)
            presets = pc['count'] if pc else 0
            root.dev_presets = presets
            if pc:
                root.set_preset_count(presets)
            cp = self._ask(0x0716, C.read_current_preset(), C.parse_current_preset)
            if cp:
                root.current_preset = min(cp['preset'], root.preset_count - 1)
        with_p = bool(presets)
        np = root.preset_count
        # --- routing / sourcesel (root level)
        if mask & D.REQ_ROUTING:
            rt = self._ask(0x0711, C.read_routing(presets), C.parse_routing,
                           fw=fw, with_preset=with_p, channel_count=root.channel_count)
            # a whole-map 0x0701 is only safe from a full read: a missing output would go out as 0
            root.routing_read = bool(rt) and len(rt['input_ids']) >= root.channel_count
            if rt:
                root.input_map = {ch: v for ch, v in enumerate(rt['input_ids'])}
        if mask & D.REQ_SOURCESEL:
            ss = self._ask(0x0713, C.read_sourcesel(), C.parse_sourcesel)
            if ss:
                root.sourcesel = ss['value']
        # --- preset-level steps
        if mask & D.REQ_MASTER_PROPS:
            for p in range(np):
                m = self._ask(0x0710, C.read_master(presets, p), C.parse_master, fw=fw, with_preset=with_p)
                if m and 'gain_raw' in m:
                    root.presets[p].master.gain_db = gain_db(m['gain_raw'], fw)
        if mask & D.REQ_MASTER_EQ:
            for p in range(np):
                mp = self._ask(0x0714, C.read_master_points(presets, p), C.parse_master_points, fw=fw, with_preset=with_p)
                if mp:
                    root.presets[p].master.eq.points = [M.EqPoint(*pt) for pt in mp['points']]
        if mask & D.REQ_INPUT_GAINS:
            for p in range(np):
                ig = self._ask(0x0714, C.read_input_gains(presets, p), C.parse_input_gains, fw=fw, with_preset=with_p)
                # the exe keeps a dev-struct table (zero at start) that the reply fills, then
                # decodes ALL k = 1..64 through FUN_1400ece60 (clamp -48..+9), MODEL_REGION §2.1
                dev = self.dev_input_raw.setdefault(p, [0] * 65)
                if ig:
                    for k, raw in ig['raws'].items():
                        if 1 <= k <= 64:
                            dev[k] = raw
                unity = 0xED00 if fw > 0xCF else 0xE700
                for k in range(1, 65):
                    db = (dev[k] - unity) * 0.001953125
                    root.input_gains[p][k] = min(max(db, M.INPUT_GAIN_MIN), M.INPUT_GAIN_MAX)
        if mask & D.REQ_INPUT_DELAY:
            for p in range(np):
                dl = self._ask(0x0714, C.read_input_delay(presets, p), C.parse_input_delay, with_preset=with_p)
                if dl:
                    root.input_delay_ms[p] = dl['us'] / 1000.0
        # --- per channel: 0x0710, 0x0714 sub 2, 3, 5, each over the presets
        for ch in range(root.channel_count):
            self._read_channel(root, ch, fw, presets, mask)
        # --- features: 0x070f sub 0..3 (0x0706 head unit is read only for its dirty bits: none at connect)
        if mask & D.REQ_FEATURE0:
            for p in range(np):
                u0 = self._ask(0x070F, C.read_upmix0(fw, presets, p), C.parse_upmix0)
                if u0:
                    root.feature_raw[('upmix0', p)] = u0
        if mask & D.REQ_FEATURE1:
            for slot in (0, 1):
                t = self._ask(0x070F, C.read_table(slot), C.parse_table)
                if t:
                    root.feature_raw[('table', slot)] = t
        if mask & D.REQ_UPMIX:
            for p in range(np):
                u = self._ask(0x070F, C.read_upmix(fw, presets, p), C.parse_upmix)
                if u:
                    pr = root.presets[p]
                    pr.upmix.enabled, pr.upmix.algorithm = bool(u['b0']), u['b1']
                    pr.upmix.lmsrate, pr.upmix.adprate, pr.upmix.adpgamma = u['fa'], u['fb'], u['fc']
        if mask & D.REQ_TIMEALIGN and fw >= 0xD0:
            for p in range(np):
                t = self._ask(0x070F, C.read_timealign(fw, presets, p), C.parse_timealign)
                if t:
                    ta = root.presets[p].timealign
                    ta.units = 1 if t['units'] else 0
                    ta.closest = t['closest_x100'] / 100.0
                    ta.furthest = t['furthest_x100'] / 100.0
        # --- FUN_140099030: settings keys from the product flags, then (fw > 209)
        # DspParamRead(0xc) -> keys 3..5 (Loudness, Bass->Sub, Rear Fill)
        root.options[1] = bool(flags >> 16 & 1)
        root.set_option(2, bool(flags >> 17 & 1))    # re-runs FUN_1400eeb10 (mode / point cap)
        if fw > 0xD1:
            pr = self._ask(0x0718, C.block_0718(0xC), C.parse_block_0718)
            if pr:
                for bit, key in ((0x1, 3), (0x2, 4), (0x4, 5)):
                    root.options[key] = bool(pr['dw'] & bit)
        root.clear_dirty()
        root.connected = True
        return root

    def _read_channel(self, root: M.DspModel, ch: int, fw: int, presets: int, mask: int) -> None:
        with_p = bool(presets)
        np = root.preset_count
        recs = [None] * np
        props = [None] * np
        filt = [None] * np
        pts = [None] * np
        for p in range(np):
            if mask & D.REQ_CHAN_PROPS:
                recs[p] = self._ask(0x0710, C.read_channel(ch, presets, p), C.parse_channel, fw=fw, with_preset=with_p)
        for p in range(np):
            if mask & D.REQ_CHAN_APPPROPS:
                props[p] = self._ask(0x0714, C.read_chan_props(ch, presets, p), C.parse_chan_props, fw=fw, with_preset=with_p)
        for p in range(np):
            if mask & D.REQ_CHAN_FILTER:
                filt[p] = self._ask(0x0714, C.read_filter(ch, presets, p), C.parse_filter, fw=fw, with_preset=with_p)
        for p in range(np):
            if mask & D.REQ_CHAN_EQ:
                pts[p] = self._ask(0x0714, C.read_chan_points(ch, presets, p), C.parse_chan_points, fw=fw, with_preset=with_p)
        for p in range(np):
            self._apply_channel(root, p, ch, recs[p], props[p], filt[p], pts[p])

    def _apply_channel(self, root, p, ch, rec, props, f, pts) -> None:
        if not props:
            return
        role = M.ROLE_BY_ORDINAL.get(props['ordinal'])
        if not role:
            return                                   # channel not mapped on the device
        if p == 0 or root.channel_for_role(role) < 0:
            root.map_speaker(role, ch)
        s = root.speaker(role, p)
        if rec is not None:
            s.gain_db = gain_db(rec['gain_raw'], root.fw)
            s.invert_phase = bool(rec['invert'])
            s.passthrough = bool(rec['passthru'] & 1)        # FUN_1400f38e0(spk, byte & 1)
            s.passthrough_raw = rec['passthru'] & 0xFF
            s.mute = bool(rec['mute'])
            s.testtone = rec['testtone']
            s.delay_ms = rec['delay_us'] / 1000.0
            s.limit_dbfs = limiter_dbfs(rec.get('limiter_raw', LIMITER_OFF))
        if props['xoverlink'] and s.xover_partner is None:
            root.link_xover_partner(s)
        if f:
            s.filter_type, s.filter_design = f['type'], f['design']
            s.f1, s.f2, s.slope1, s.slope2 = f['f1'], f['f2'], f['s1'], f['s2']
        if pts:
            s.eq.points = [M.EqPoint(*pt) for pt in pts['points']]
            for pt in s.eq.points:
                pt.app_biquad = s.eq.mode == M.MODE_BIQUAD


def read_tune(exchange, root: M.DspModel | None = None, product_id: int | None = None, log=None,
              read_mask: int | None = None):
    """Convenience: read everything into `root` (a fresh DspModel by default)."""
    root = root if root is not None else M.DspModel()
    r = Reader(exchange, log)
    r.read_tune(root, product_id, read_mask)
    # Distances are not stored on the device: derive each from the preset's
    # furthest point and the channel delay (FUN_1400f5890: dist = furthest - k*delay),
    # keeping the closest/furthest the device reported.
    for pr in root.presets:
        t = pr.timealign
        k = M.IN_PER_MS if t.units == 1 else M.CM_PER_MS
        for s in root.speakers_in_preset(pr.index):
            s.distance = max(0.0, t.furthest - k * s.delay_ms)
    root.clear_dirty()
    return root, r.sent
