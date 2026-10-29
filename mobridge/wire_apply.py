"""Turn a control change into the exact frames the official app sends.

Only the writes proven IDENTICAL (decompile + capture) are built here; see
parity.proven_write for the grammar each must satisfy. Anything else
(0x0700 full record, crossover definitions) is deliberately absent so it cannot
be sent by accident. Returns framed wire bytes, ready for Transport.write.
"""
from __future__ import annotations

from . import biquad, channel_record, eq, filterbins
from .framing import MdiFramer
from .message import MdiMessage

_F = MdiFramer()


def _frame(cmd: int, payload: bytes) -> bytes:
    return _F.encode(MdiMessage(cmd=cmd, data=payload))


def gain_frames(channel: int, gain_db: float, preset: int = 0,
                master_eq=()) -> list[bytes]:
    """MASTER gain, the captured 3-frame sequence: the LEN4 0x0700 frame
    (0xECCD/-0.1 and 0xED00/0.0 byte-exact), the 0x0702 master filter-bins table
    (~16 ms later), then the 0x0704 sub06 master EQ list (~80 ms; empty list =
    the captured aa000207040600ed). Pass the real master EQ in `master_eq`;
    never send an empty list over an existing master EQ.

    Channel gain goes in the LEN16 record instead (record_frames): the app never
    sends LEN4 for a channel."""
    if channel != channel_record.MASTER_CHANNEL:
        raise ValueError('LEN4 gain is the master only; use record_frames for channel %d' % channel)
    master_eq = list(master_eq)
    return [_frame(channel_record.CMD_CHANNEL_PROPERTY,
                   channel_record.build_gain_only(channel, gain_db, preset=preset)),
            _frame(filterbins.CMD_FILTER_BINS, filterbins.build_0702(0, master_eq, preset=preset)),
            _frame(eq.CMD_FILTER, eq.build_master_points(master_eq, preset=preset))]


def record_frames(channel: int, *, gain_db: float = 0.0, delay_ms: float = 0.0,
                  limiter_dbfs: float | None = None, phase: bool = False,
                  passthrough: bool = False, mute: bool = False,
                  preset: int = 0, bands=(), crossover: list[list[float]] | None = None,
                  mode1_crossover: bool = False) -> list[bytes]:
    """A channel property change, the captured 4-frame sequence (exe
    FUN_14017f4c0): the LEN16 0x0700 record, then within ~15 ms the channel's
    0x0702 filter-bins table, ~60 ms later its 0x0708 biquad chain (a zero stage
    when there is no crossover), then the 0x0704 sub05 point list. Test-tone
    bytes [9-11] stay 0. `bands`/`crossover` are the channel's CURRENT EQ, which
    the sequence re-sends unchanged."""
    flags = {channel_record.PHASE_OFFSET: int(phase),
             channel_record.PASSTHRU_OFFSET: int(passthrough),
             channel_record.MUTE_OFFSET: int(mute)}
    bands = list(bands)
    return [_frame(channel_record.CMD_CHANNEL_PROPERTY,
                   channel_record.build_record(channel, preset=preset, gain_db=gain_db,
                                               delay_ms=delay_ms, limiter_dbfs=limiter_dbfs,
                                               flags=flags))] + \
        _eq_tail(channel, bands, crossover, preset, mode1_crossover)


def _eq_tail(channel, bands, crossover, preset, mode1_crossover):
    return [_frame(filterbins.CMD_FILTER_BINS,
                   filterbins.build_0702(channel, bands, preset=preset, mode1_crossover=mode1_crossover))] \
        + eq_frames(channel, bands, crossover, preset)


def eq_frames(channel: int, bands, crossover: list[list[float]] | None = None,
              preset: int = 0) -> list[bytes]:
    """A channel's WHOLE EQ, in the captured order: the 0x0708 biquad chain
    (crossover stages or a zero stage, then one peaking stage per band, in list
    order), then the 0x0704 sub05 point list with every band. No 0x0702 here:
    the app sends the filter-bins table after GAIN writes only (19 of 19 in the
    vehicle capture), never after an EQ edit. Each frame replaces the device's
    list, so all bands must go together. Band gains/Qs must be the app's
    internal values (the half-floats on the wire are rounded copies)."""
    bands = list(bands)
    return [_frame(biquad.CMD_BIQUAD, biquad.build_0708(channel, biquad.chain_stages(bands, crossover), preset)),
            _frame(eq.CMD_FILTER, eq.build_eq_points(channel, bands, preset))]


def eq_band_frames(channel: int, freq_hz: float, gain_db: float, q: float) -> list[bytes]:
    """One-band EQ (N = 1). Only right when the channel has exactly this one band."""
    return eq_frames(channel, [(freq_hz, gain_db, q)])


def eq_band_remove_frames(channel: int) -> list[bytes]:
    """Clear the channel's EQ: zero biquad chain + empty point list (captured pair)."""
    return eq_frames(channel, [])


def debug_send_frames(line: str) -> list[bytes]:
    """Console text as the app sends it: one 0x0401 frame per byte, CR-terminated."""
    data = line.rstrip('\r\n').encode('latin-1') + b'\r'
    return [_frame(0x0401, bytes([b])) for b in data]
