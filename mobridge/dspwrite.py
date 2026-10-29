"""Dirty bits -> request mask -> frames, in the official app's DspWrite order.

Mirrors FUN_1400f95f0 (model -> device struct + request mask, UI_REGION_2.md
§1.6), FUN_1400fa060 / f9cb0 / f8580 (per-speaker / master / root encoders,
MODEL_REGION.md §2) and the DspWrite job's step order captured on the vehicle
(EXE_DEEP_RE_OCT22.md §8; the step table itself is data the decompile could not
read). Payload layouts are from CMD_TABLE.md §3.

Coalescing (UI_REGION_2 §1.2): the official app has no per-field debounce. Each
model update queues one flush; the flush takes EVERY dirty bit of every preset
and channel at that instant into one job, then sleeps 250 ms. `flush(model)`
does exactly that: it returns the whole ordered frame list and clears the bits.

Nothing here transmits. Like DspWrite (FUN_1400aed20), the request mask is
ANDed down by the product's flag word (products.request_filter) and only the
frames whose request bit survives are kept.
"""
from __future__ import annotations

import struct as _struct

from . import biquad, channel_record, eq, filterbins
from .framing import MdiFramer
from .message import MdiMessage
from . import model as M

# request-mask bits (sess+0x38)
REQ_FEATURE0, REQ_FEATURE1, REQ_UPMIX, REQ_TIMEALIGN = 0x1, 0x2, 0x4, 0x8
REQ_HEADUNIT = 0x10
REQ_MASTER_PROPS, REQ_CHAN_PROPS, REQ_PRESET, REQ_ROUTING = 0x10000, 0x20000, 0x40000, 0x80000
REQ_SOURCESEL, REQ_CHAN_BINS, REQ_CHAN_IIR, REQ_MASTER_BINS = 0x100000, 0x200000, 0x400000, 0x800000
REQ_INPUT_GAINS, REQ_INPUT_DELAY, REQ_NAME, REQ_FW = 0x1000000, 0x2000000, 0x4000000, 0x8000000
REQ_CHAN_APPPROPS, REQ_CHAN_EQ, REQ_CHAN_FILTER, REQ_MASTER_EQ = 0x10000000, 0x20000000, 0x40000000, 0x80000000
REQ_WIPE_CHANNEL = 0x703a0000       # a channel in the device but not in the model

# DspRead step order (EXE_DEEP_RE_OCT22 §8 [CAP]): (cmd, payload) the connect read sends
READ_ORDER = [
    (0x0714, b'\x01'),            # device name (AppName)
    (0x0714, b'\x00'),            # fw version (AppVersion)
    (0x0716, b'\x02'),            # preset count
    (0x0716, b'\x03'),            # current preset
    (0x0711, b'\x00'),            # routing
    (0x0713, b''),                # sourcesel byte
    (0x0710, b'\x00\x00'),        # master props (preset, ch 0)
    (0x0714, b'\x06\x00'),        # master EQ points
    (0x0714, b'\x07\x00'),        # input gains
    (0x0714, b'\x08\x00'),        # input delay
    # then per channel: 0x0710 [p][ch+1], 0x0714 02, 0x0714 03, 0x0714 05; then 0x070f sub0..3, 0x0706
]


_F = MdiFramer()


def _frame(cmd: int, payload: bytes) -> bytes:
    return _F.encode(MdiMessage(cmd=cmd, data=payload))


class Flush:
    """Result of one DspWrite: ordered frames plus the masks the exe would have
    put in the job struct, and anything that could not be built."""

    def __init__(self):
        self.frames: list[tuple[int, bytes]] = []       # (cmd, payload) in send order
        self.mask = 0
        self.preset_mask = 0
        self.channel_mask = 0
        self.param_write = None                           # (0xc, option bits) -> DspParamWrite
        self.skipped: list[str] = []

        self.bits: list[int] = []                         # request bit of each frame

    def add(self, cmd: int, payload: bytes, bit: int = 0):
        self.frames.append((cmd, payload))
        self.bits.append(bit)
        self.mask |= bit

    def filter(self, allowed: int) -> None:
        """Keep only the frames whose request bit is in `allowed` (FUN_1400ac190)."""
        keep = [i for i, b in enumerate(self.bits) if not b or b & allowed]
        self.frames = [self.frames[i] for i in keep]
        self.bits = [self.bits[i] for i in keep]
        self.mask &= allowed

    def wire(self) -> list[bytes]:
        return [_frame(c, p) for c, p in self.frames]

    def cmds(self) -> list[int]:
        return [c for c, _p in self.frames]


# --------------------------------------------------------------------------
# payload builders (CMD_TABLE.md §3)
# --------------------------------------------------------------------------
class Held(NotImplementedError):
    """A frame whose bytes for this firmware are not fully pinned by the decompile:
    never sent (PARITY SAFETY RULE); the flush lists it in Flush.skipped."""


def dev_presets(root: M.DspModel) -> int:
    """dev+0x46: the preset count the DEVICE reported (0x0716 sub 2); 0 = no preset byte."""
    v = getattr(root, 'dev_presets', None)
    return root.preset_count if v is None else v


def _drop_preset(root: M.DspModel, payload: bytes, at: int) -> bytes:
    """Every per-preset builder sends [u8 preset] only if dev+0x46 != 0 (CMD_TABLE §3)."""
    return payload if dev_presets(root) else payload[:at] + payload[at + 1:]


def name_payload(name: str) -> bytes:                               # 0x0704 sub 1
    raw = name.encode('utf-8', 'replace')[:0x3F]
    return b'\x01' + raw.ljust(0x40, b'\x00')


def select_preset_payload(preset: int) -> bytes:                    # 0x0716 sub 1
    return bytes([0x01, preset & 0xFF])


def routing_payload(root: M.DspModel) -> bytes:                     # 0x0701
    out = b'\x00' if dev_presets(root) else b''
    for i in range(root.channel_count):
        v = root.input_map.get(i, 0)
        out += (v & 0xFFFFFFFFFFFFFFFF).to_bytes(8, 'big') if root.fw >= 0xCD else bytes([v & 0xFF])
    return out


def sourcesel_payload(root: M.DspModel) -> bytes:                   # 0x0703
    return bytes([root.sourcesel & 0xFF])


def master_props_payload(root: M.DspModel, preset: int) -> bytes:   # 0x0700 ch 0 (LEN4)
    from . import cmdspec as C
    raw = M.gain_raw(root.presets[preset].master.gain_db, root.fw) & 0xFFFF
    legacy = (0, 0, 0, 0)
    if root.fw == 100:
        # FUN_1401803e0: hi bytes of the per-preset table entries k = 2, 5, 3, 4
        # (dev+0x261/0x279/0x269/0x271 + p*2; DSP_FIELD_MEANINGS 15.3)
        unity = M.gain_unity(root.fw)
        legacy = tuple((int(round(unity + root.input_gains[preset][k] * 512.0)) & 0xFFFF) >> 8 for k in (2, 5, 3, 4))
    return C.write_master(raw, root.fw, dev_presets(root), preset, legacy)


def master_bins_payload(root: M.DspModel, preset: int) -> bytes:    # 0x0702 ch 0
    return _drop_preset(root, _master_bins(root, preset), 0)


def _master_bins(root: M.DspModel, preset: int) -> bytes:
    return filterbins.build_0702(0, root.presets[preset].master.eq.tuples(), preset=preset,
                                 **_bins_fmt(root))


def master_points_payload(root: M.DspModel, preset: int) -> bytes:  # 0x0704 sub 6
    from . import cmdspec as C
    if root.fw < 200:
        return C.write_master_points(root.presets[preset].master.eq.tuples(), root.fw, dev_presets(root), preset)
    return _drop_preset(root, eq.build_master_points(root.presets[preset].master.eq.tuples(), preset=preset), 1)


def input_gains_payload(root: M.DspModel, preset: int) -> bytes:    # 0x0704 sub 7
    unity = M.gain_unity(root.fw)
    out = bytes([0x07, preset & 0xFF])
    n, k0 = (0x40, 1) if root.fw > 0xCB else (8, 0)
    for k in range(k0, k0 + n):
        raw = int(round(unity + root.input_gains[preset][k if k <= 64 else 64] * 512.0)) & 0xFFFF
        out += raw.to_bytes(2, 'big') if root.fw >= 0xCA else bytes([raw >> 8])
    return out


def input_delay_payload(root: M.DspModel, preset: int) -> bytes:    # 0x0704 sub 8
    f = _struct.unpack('f', _struct.pack('f', root.input_delay_ms[preset]))[0]
    v = int(f * 1000.0)                                              # (ushort)(int)(float * 1000.0f)
    v = min(v & 0xFFFF, 10000)
    return bytes([0x08, preset & 0xFF]) + v.to_bytes(2, 'big')


def channel_props_payload(root: M.DspModel, s: M.SpeakerModel, channel: int) -> bytes:   # 0x0700
    """FUN_14017f250 record: preset, ch+1, u16 gain, u8 phase, u16 delay us, u8
    passthrough, u8 mute, u16 testtone, u8 testtone type, i32 limiter."""
    unity = M.gain_unity(root.fw)
    buf = bytearray(16)
    buf[0] = s.preset & 0xFF
    buf[1] = (channel + 1) & 0xFF
    buf[2:4] = (M.gain_raw(s.gain_db, root.fw) & 0xFFFF).to_bytes(2, 'big')
    buf[4] = 1 if s.invert_phase else 0
    us = int(round(s.delay_ms * 1000.0)) & 0xFFFF                   # (short)(int)round(ms*1000): wraps
    buf[5:7] = us.to_bytes(2, 'big')
    if root.fw >= 0xD1:                                              # raw rec+0x46e, only bit 0 toggled
        buf[7] = (getattr(s, 'passthrough_raw', 0) & 0xFE) | (1 if s.passthrough else 0)
    else:
        buf[7] = 1 if s.passthrough else 0
    buf[8] = 1 if s.mute else 0
    buf[9:11] = (s.testtone & 0xFFFF).to_bytes(2, 'big')
    buf[11] = s.testtone_type & 0xFF
    buf[12:16] = channel_record.limiter_raw(s.limit_dbfs).to_bytes(4, 'big')
    if root.fw < 0xCA:                                               # u8 gain (hi byte) before fw 202
        buf = buf[:2] + buf[2:3] + buf[4:]
        n = 11
    else:
        n = 12
    if root.fw <= 0xCD:                                              # limiter only sent for fw > 205
        buf = buf[:n]
    del unity
    return _drop_preset(root, bytes(buf), 0)


# Mode-1 (tweeter/mid) tables: the DSPFilters curve matches the exe thread's
# reference 720/720, and the three constants the port assumed were read from
# the exe's .rdata on 2026-10-22 15:39Z (DAT_14231c0c8 = 0.7, c0d0 = 0.99,
# c0d8 = 1.01). With the project's rule now "decompile alone" (14:16Z) nothing is
# left unpinned, so the producer is open. Set False to re-lock.
MODE1_UNLOCKED = True


def channel_bins_payload(root: M.DspModel, s: M.SpeakerModel, channel: int) -> bytes:    # 0x0702
    return _drop_preset(root, _channel_bins(root, s, channel), 0)


def _channel_bins(root: M.DspModel, s: M.SpeakerModel, channel: int) -> bytes:
    mode1 = s.mode == M.MODE_DEVICE_FILTER and s.filter_type != M.FT_NONE
    if mode1 and not MODE1_UNLOCKED:
        raise NotImplementedError('mode-1 crossover table is decompile-only (no capture yet): locked')
    xo = (s.filter_type, s.filter_design, s.f1, s.f2, s.slope1, s.slope2) if mode1 else False
    return filterbins.build_0702(channel + 1, s.eq.tuples(), preset=s.preset,
                                 mode1_crossover=xo, **_bins_fmt(root))


def _bins_fmt(root: M.DspModel) -> dict:
    if root.fw < 0xCF:
        return {'unity_raw': filterbins.UNITY_RAW_LEGACY, 'legacy': True}
    return {'unity_raw': M.gain_unity(root.fw)}


def channel_iir_payload(s: M.SpeakerModel, channel: int, root: M.DspModel | None = None) -> bytes:   # 0x0708
    out = biquad.build_0708(channel + 1, s.biquad_chain(), preset=s.preset)
    return out if root is None else _drop_preset(root, out, 0)


def channel_appprops_payload(root: M.DspModel, s: M.SpeakerModel, channel: int) -> bytes:  # 0x0704 sub 2
    out = bytes([0x02, s.preset & 0xFF, (channel + 1) & 0xFF, M.ORDINAL_BY_ROLE.get(s.role, 0)])
    if root.fw < 200:
        out += b'\x00'
    out += bytes([1 if s.xover_partner is not None else 0])
    if root.fw < 200:
        out += _struct.pack('>d', 0.0) + b'\x00\x00\x00\x00\x00'
    return _drop_preset(root, out, 1)


def channel_filter_payload(root: M.DspModel, s: M.SpeakerModel, channel: int) -> bytes:   # 0x0704 sub 3
    out = bytes([0x03, s.preset & 0xFF, (channel + 1) & 0xFF, s.filter_type & 0xFF, s.filter_design & 0xFF])
    if root.fw < 200:
        out += _struct.pack('>dddd', s.f1, s.f2, s.slope1, s.slope2)
    else:
        out += _struct.pack('>HHBB', s.f1 & 0xFFFF, s.f2 & 0xFFFF, s.slope1 & 0xFF, s.slope2 & 0xFF)
    if root.fw in (0xC9, 0xCA):
        # stage-0 biquad b0 b1 b2 (rec+0x900..) then a1 a2 (rec+0x878..), DSP_FIELD_MEANINGS 15.1
        chain = s.biquad_chain()
        if not chain:
            raise Held('fw 201/202 filter record: no stage-0 biquad to send')
        out += _struct.pack('>5f', *chain[0][:5])
    return _drop_preset(root, out, 1)


def channel_points_payload(s: M.SpeakerModel, channel: int, root: M.DspModel | None = None) -> bytes:   # 0x0704 sub 5
    from . import cmdspec as C
    if root is not None and root.fw < 200:
        return C.write_chan_points(channel, s.eq.tuples(), root.fw, dev_presets(root), s.preset)
    out = eq.build_eq_points(channel + 1, s.eq.tuples(), preset=s.preset)
    return out if root is None else _drop_preset(root, out, 1)


def headunit_payloads(hu: M.HeadunitModel) -> list[bytes]:                                 # 0x0705
    """One frame per dirty value, lowest index first: [index][value]."""
    out = []
    for i, f in enumerate(hu.FIELDS[:6]):        # FUN_1400f9b00 never syncs bit 0x40 (subgain)
        if hu.flags & (1 << i):
            out.append(bytes([i, hu.values[f] & 0xFF]))
    return out


def upmix_payload(root: M.DspModel, u: M.UpmixModel) -> bytes:                            # 0x0707 sub 2
    out = b'\x02' + (bytes([u.preset & 0xFF]) if dev_presets(root) and root.fw >= 0xD1 else b'')
    return out + bytes([1 if u.enabled else 0, u.algorithm & 0xFF]) + _struct.pack('>fff', u.lmsrate, u.adprate, u.adpgamma)


TA_DISTANCE_SCALE = 100.0   # DAT_1401fdd28 = 100.0, read from .rdata 2026-10-22 (00 00 00 00 00 00 59 40)


def timealign_payload(root: M.DspModel, t: M.TimeAlignModel) -> bytes:                    # 0x0707 sub 3
    out = b'\x03' + (bytes([t.preset & 0xFF]) if dev_presets(root) and root.fw >= 0xD1 else b'')
    return out + bytes([1 if t.units == 1 else 0]) + _struct.pack(
        '>HH', int(round(t.closest * TA_DISTANCE_SCALE)) & 0xFFFF, int(round(t.furthest * TA_DISTANCE_SCALE)) & 0xFFFF)


# --------------------------------------------------------------------------
# the flush
# --------------------------------------------------------------------------
_CHAN_PROPS_BITS = (M.SYNC_INVERT_PHASE | M.SYNC_GAIN | M.SYNC_PASSTHROUGH | M.SYNC_MUTE
                    | M.SYNC_TESTTONE | M.SYNC_TIME | M.SYNC_LIMIT)


def flush(root: M.DspModel, clear: bool = True, product_flags: int | None = None,
          hold_routing: bool = False) -> Flush:
    """FUN_140099a80 -> FUN_1400f95f0 -> DspWrite: every dirty bit of every
    preset/channel into one ordered frame list, filtered by the product flags
    (default: the flags of root.product in the exe's product table).
    hold_routing: leave 0x0701 out (the engine sets it while connected without a
    successful 0x0711 read: the whole-map write would zero unread outputs)."""
    from . import products
    out = Flush()
    rf = root.root_flags
    add = out.add

    def guarded(cmd, make, bit, label):
        try:
            add(cmd, make(), bit)
        except Held as e:
            out.skipped.append('%s 0x%04x: %s' % (label, cmd, e))
    # --- root level (FUN_1400f8580)
    if rf & M.ROOT_NAME:
        out.add(0x0704, name_payload(root.name), REQ_NAME)
    if rf & M.ROOT_PRESET and dev_presets(root):             # CMD_TABLE §3.3: only with dev+0x46
        out.add(0x0716, select_preset_payload(root.current_preset), REQ_PRESET)
    any_map = any(s.sync & M.SYNC_MAP for p in root.presets for s in p.speakers.values())
    if (rf & M.ROOT_INPUT_MAP or any_map) and hold_routing:
        out.skipped.append('routing 0x0701: input map not read from the device on this connection')
    elif rf & M.ROOT_INPUT_MAP or any_map:               # SyncMap requests 0x10180000 = sub2 + 0x0703 + 0x0701
        out.add(0x0701, routing_payload(root), REQ_ROUTING)
    if rf & M.ROOT_SOURCESEL or any_map:
        out.add(0x0703, sourcesel_payload(root), REQ_SOURCESEL)
    # --- per preset
    for p in range(root.preset_count):
        pr = root.presets[p]
        touched = False
        mf = pr.master.flags
        if mf & M.MASTER_GAIN:
            guarded(0x0700, lambda: master_props_payload(root, p), REQ_MASTER_PROPS, 'master'); touched = True
        if mf & M.MASTER_BINS:
            guarded(0x0702, lambda: master_bins_payload(root, p), REQ_MASTER_BINS, 'master'); touched = True
        if mf & M.MASTER_EQ:
            guarded(0x0704, lambda: master_points_payload(root, p), REQ_MASTER_EQ, 'master'); touched = True
        if rf & M.ROOT_INPUT_GAINS:
            out.add(0x0704, input_gains_payload(root, p), REQ_INPUT_GAINS if root.fw != 100 else REQ_MASTER_PROPS); touched = True
        if rf & M.ROOT_INPUT_DELAY:
            out.add(0x0704, input_delay_payload(root, p), REQ_INPUT_DELAY); touched = True
        for ch in root.channels():
            s = root.speaker(root.channel_role[ch], p)
            sb = s.sync
            if not sb:
                continue
            touched = True
            out.channel_mask |= 1 << ch
            if sb & _CHAN_PROPS_BITS:
                out.add(0x0700, channel_props_payload(root, s, ch), REQ_CHAN_PROPS)
            if sb & M.SYNC_BINS:
                try:
                    out.add(0x0702, channel_bins_payload(root, s, ch), REQ_CHAN_BINS)
                except NotImplementedError as e:
                    out.skipped.append('ch%d 0x0702: %s' % (ch + 1, e))
            if sb & M.SYNC_IIR and root.fw >= 0xCB:
                out.add(0x0708, channel_iir_payload(s, ch, root), REQ_CHAN_IIR)
            if sb & (M.SYNC_MAP | M.SYNC_XOVER_LINK):
                out.add(0x0704, channel_appprops_payload(root, s, ch), REQ_CHAN_APPPROPS)
            if sb & M.SYNC_FILTER:
                guarded(0x0704, lambda: channel_filter_payload(root, s, ch), REQ_CHAN_FILTER, 'ch%d' % (ch + 1))
            if sb & M.SYNC_EQ:
                out.add(0x0704, channel_points_payload(s, ch, root), REQ_CHAN_EQ)
            if clear:
                s.clear_sync()
        if touched:
            out.preset_mask |= 1 << p
        if clear:
            pr.master.flags = 0
    # --- head unit, upmix, time align (REQ 0x10 / 0x4 / 0x8)
    for payload in headunit_payloads(root.headunit):
        out.add(0x0705, payload, REQ_HEADUNIT)
    # FeatureUpmix over all presets, then FeatureTimeAlign; without device presets
    # (dev+0x46 == 0) the exe writes only the current one (p = dev+0x47, CMD_TABLE §3.19)
    feat = range(root.preset_count) if dev_presets(root) else [root.current_preset]
    for p in feat:
        pr = root.presets[p]
        if pr.upmix.flags:
            out.add(0x0707, upmix_payload(root, pr.upmix), REQ_UPMIX)
            if clear:
                pr.upmix.flags = 0
    for p in feat:
        pr = root.presets[p]
        if pr.timealign.flags & (M.TA_UNITS | M.TA_CLOSEST | M.TA_FURTHEST) and root.fw >= 0xD0:
            out.add(0x0707, timealign_payload(root, pr.timealign), REQ_TIMEALIGN)
        if clear:
            pr.timealign.flags = 0
    if rf & M.ROOT_OPTIONS:
        out.param_write = (0xC, root.option_bits())          # DspParamWrite(0xc, dev+0x460): DSP_FIELD_MEANINGS item 10
    if clear:
        root.headunit.flags = 0
        root.root_flags = 0
    if product_flags is None:
        product_flags = products.flags(root.product)      # 0 (nothing passes) for a product not in the table
    out.filter(products.request_filter(0xffffffff, product_flags))
    if out.param_write is not None and not product_flags & 0x2:
        out.param_write = None                            # FUN_1400ac230: settings flag 0x1000 needs flag bit 1
    if out.param_write is not None:
        from . import cmdspec
        out.add(0x0717, cmdspec.block_0717(*out.param_write))
    return out
