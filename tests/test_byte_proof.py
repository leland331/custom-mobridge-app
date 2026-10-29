"""Offline byte-proof: no device needed.

Proves that the bytes Custom Mobridge would put on the wire to connect and read
the device are byte-identical to the bytes the official app actually sent, by
replaying a recorded capture of the official app (reference_frames_full.json).

The full capture lives in the project-files root; when it isn't mounted (CI),
the tests fall back to the committed subset in reference_frames.json so the
proof still runs on the frames we ship.
"""
import os

import pytest

from mobridge import connect, parity
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage

FULL_CAPTURE = '/mnt/project-files/reference_frames_full.json'


def _capture_path():
    return FULL_CAPTURE if os.path.exists(FULL_CAPTURE) else None


def test_our_framer_reproduces_the_full_official_capture():
    path = _capture_path()
    if not path:
        pytest.skip('full capture not mounted')
    res = connect.verify_capture(path)
    assert res.total == 291, res.total
    assert res.ok, res.mismatches[:5]
    # It's a connect + read-back sniff: overwhelmingly reads, only the 4 captured
    # 0x0700 writes. Confirms we're proving the real connect path, not a fluke.
    assert res.writes == 4
    assert res.reads == res.total - 4


def test_connect_handshake_matches_the_capture_byte_for_byte():
    """The fixed connect prefix our connect builder emits == the first frames the
    official app sent, in order, byte for byte."""
    path = _capture_path()
    if not path:
        pytest.skip('full capture not mounted')
    recorded = connect.load_capture(path)
    ours = connect.handshake_frames()
    # Align on the first 0x0002 SetBaud: the official log has two null keepalives
    # before it; our handshake carries the same shape, so compare prefix-to-prefix.
    n = len(ours)
    for i, (step, wire) in enumerate(ours):
        assert wire == recorded[i].wire, (i, step.name, wire.hex(), recorded[i].wire.hex())
    # sanity: the prefix is exactly the handshake, next frame is the read sweep
    assert recorded[n].cmd in connect.READ_COMMANDS


def test_every_connect_and_read_frame_clears_the_parity_gate():
    # The whole read-only connect path is cleared to actually send once a device
    # is attached -- nothing unproven rides along.
    assert connect.handshake_is_cleared()
    f = MdiFramer()
    for cmd in connect.READ_COMMANDS:
        # a representative in-format read payload for each read command
        pl = b'\x02\x00' if cmd in (0x070F, 0x0714, 0x0710) else b''
        assert parity.frame_cleared(f.encode(MdiMessage(cmd=cmd, data=pl)), cmd), hex(cmd)


def test_flash_family_clears():
    # Firmware flashing is unlocked (23:18Z); fif.flash_problems still guards the bootblock.
    f = MdiFramer()
    for cmd in (0x0201, 0x0281):
        assert parity.frame_cleared(f.encode(MdiMessage(cmd=cmd, data=b'')), cmd)


def test_rx_decoder_roundtrips_a_synthetic_reply():
    # We have no recorded device replies offline, but the decoder must round-trip
    # a well-formed reply frame (proves the RX path is wired; real RX needs the unit).
    f = MdiFramer()
    reply = f.encode(MdiMessage(cmd=0x0710, data=bytes.fromhex('0000abcd')))
    frames = connect.decode_reply(reply)
    assert len(frames) == 1 and frames[0].kind == 'mdi'
    assert frames[0].message.cmd == 0x0710
    assert frames[0].message.data == bytes.fromhex('0000abcd')


def test_committed_subset_also_byte_proves():
    # Even without the full capture, every frame we ship in reference_frames.json
    # reproduces byte-exact (this is the CI-safe floor of the proof).
    rows = parity.check_all()
    assert rows and all(ok for _, ok, _ in rows)
