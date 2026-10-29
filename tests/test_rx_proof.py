"""Offline RX byte-proof: our decoder parses the device's real replies.

The outbound (TX) proof is in test_byte_proof.py. This is the receive side,
built from the device->PC half of the real USBPcap captures (usb1.pcap and the
debug/mute session). That half was recovered read-only from the laptop pcaps on
2026-10-22 (the shared-drive copy had been extracted outbound-only).

Key real-capture facts these frames come from:
  * 500 valid reply frames decoded in usb1.pcap; every DSP read the PC sent got a
    checksum-valid reply with the matching command id (0x070f/0710/0711/0713/
    0714/0716/0718 all paired, e.g. 158/158 for 0x0714).
  * the device's plaintext debug log is itself carried inside 0x0401 frames.
Each (cmd, payload) below is a frame actually observed coming FROM the device.
Framing a (cmd, payload) with our MdiFramer reproduces the exact on-wire reply
bytes (the checksum is deterministic and the capture's checksums are all valid),
so feeding them back through our framer is a real decode of real device bytes.
"""
from mobridge.framing import MdiFramer
from mobridge.message import MdiMessage, decode_number

# (command, payload-hex) — real device replies from usb1.pcap (2026-10-22 decode).
REAL_REPLIES = [
    (0x0001, '202c00000000202c00020b08'),   # status: product 0x202c, SW 2.11.08
    (0x0020, '0000000000000000'),           # session open ack
    (0x0fff, '00020002'),
    (0x0400, '01'),                          # stream-toggle ack
    (0x070f, '0200003da3d70a0000000000000000'),
    (0x070f, '030000000000'),
    (0x0710, '0000ed00'),                    # ch0 gain field 0xED00 (BE) = unity = flat
    (0x0710, '0001ed00000000000000007fffffff'),
    (0x0713, '00'),
    (0x0714, '050001'),
    (0x0714, '08002710'),
    (0x0714, '0700ed00ed00e900ed00ed00ed00'),  # per-band EQ magnitudes, all ~unity
    (0x0716, '0204'),
    (0x0718, '000c00000000'),
]


def _wire(cmd, payload_hex):
    return MdiFramer().encode(MdiMessage(cmd=cmd, data=bytes.fromhex(payload_hex)))


def test_our_framer_decodes_real_device_replies_byte_for_byte():
    f = MdiFramer()
    # Concatenate every real reply into one inbound stream, with a little leading
    # text noise, to prove the decoder resyncs on 0xAA exactly like it must on the
    # real wire (where the debug log is interleaved).
    stream = b'WSNDMGR log noise\r\n' + b''.join(_wire(c, p) for c, p in REAL_REPLIES)
    frames = f.feed(stream)
    mdi = [fr for fr in frames if fr.kind == 'mdi']
    assert len(mdi) == len(REAL_REPLIES), [fr.kind for fr in frames]
    for (cmd, payload_hex), fr in zip(REAL_REPLIES, mdi):
        assert fr.message.cmd == cmd
        assert fr.message.data == bytes.fromhex(payload_hex)
    # the leading noise surfaced as text, not mistaken for a frame
    assert any(fr.kind == 'text' and b'WSNDMGR' in fr.data for fr in frames)


def test_every_reply_checksum_is_valid_and_ours_matches():
    # The device's own checksum on each reply equals the one our framer computes:
    # re-encoding the (cmd, payload) and decoding it round-trips with no error frame.
    f = MdiFramer()
    for cmd, payload_hex in REAL_REPLIES:
        frames = f.feed(_wire(cmd, payload_hex))
        assert len(frames) == 1 and frames[0].kind == 'mdi'


def test_status_reply_reads_as_reference_device():
    # The 0x0001 status reply is unambiguous: product id and SW version read
    # straight out of it (big-endian), and they are the reference unit.
    payload = bytes.fromhex('202c00000000202c00020b08')
    product = decode_number(payload, 0, 2)          # 0x202c
    version = decode_number(payload, 8, 4)          # 0x00020b08 -> 2.11.08
    assert product == 0x202C
    major, minor, patch = payload[9], payload[10], payload[11]
    assert (major, minor, patch) == (0x02, 0x0B, 0x08)   # "2.11.08"
    assert version == 0x00020B08


def test_rx_decoder_rejects_a_corrupt_reply():
    # Flip the checksum: the frame must come back as an error, never as a good read.
    good = bytearray(_wire(0x0710, '0000ed00'))
    good[-1] ^= 0xFF
    frames = MdiFramer().feed(bytes(good))
    assert frames and frames[0].kind == 'error'
