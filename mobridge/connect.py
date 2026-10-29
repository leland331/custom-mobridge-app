"""The read-only connect sequence, and an offline byte-proof against a capture.

This is the thing the user asked to "byte prove without the unit": the exact
ordered frames Custom Mobridge would put on the wire when it connects and reads
the device back. Every frame here is READ-ONLY -- handshake, baud, stream
toggle, and DSP value reads. Nothing in this module persists anything on the
device, and every frame is cleared by the parity gate (parity.py).

`handshake_frames()` builds the fixed connect prefix. `verify_capture()` loads a
recorded capture of the official app and proves, frame for frame, that the bytes
our framer produces are byte-identical to the bytes the official app actually
sent. No device needed: the capture is the reference.

What this can and cannot prove offline:
  * TX (what we SEND) is fully provable -- the capture is a recording of the
    official app's outbound frames, so a byte match is a real parity proof.
  * RX (decoding what the device SENDS BACK) needs device-reply bytes. The
    capture artifacts in the project are OUTBOUND-only (PC->device), so reply
    decoding is exercised here only against synthetic frames; proving it against
    real replies needs the raw RX stream (on the laptop) or the unit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import parity
from .framing import Frame, MdiFramer
from .message import MdiMessage

# Negotiated UART speed the app switches to (0x0002 payload 00 03 84 00).
CONNECT_BAUD = 230400


@dataclass
class Step:
    name: str
    cmd: int
    payload: bytes = b''
    note: str = ''

    def message(self) -> MdiMessage:
        return MdiMessage(cmd=self.cmd, data=self.payload)


# The fixed connect handshake, exactly as captured from the official app
# (reference_frames_full.json, 2026-10-21). Read-only throughout.
HANDSHAKE: list[Step] = [
    Step('keepalive',    0x0000, b'',                 'null keepalive'),
    Step('keepalive',    0x0000, b''),
    Step('set_baud',     0x0002, bytes.fromhex('00038400'), 'SetBaud 230400'),
    Step('keepalive',    0x0000, b''),
    Step('keepalive',    0x0000, b''),
    Step('set_baud',     0x0002, bytes.fromhex('00038400'), 'SetBaud 230400 (repeat)'),
    Step('ping',         0x0001, b'',                 'ping / SW version'),
    Step('session_open', 0x0020, b''),
    Step('handshake',    0x0025, b''),
    Step('stream_on',    0x0400, bytes.fromhex('01'), 'enable debug/log stream'),
    Step('ping',         0x0001, b''),
    Step('get_config',   0x0282, b'',                 'request config file'),
]

# DSP value-read commands the connect read-back sweep uses. All non-persisting;
# cleared at the command level in parity.SEND_SAFE_COMMANDS.
READ_COMMANDS = (0x070F, 0x0710, 0x0711, 0x0713, 0x0714, 0x0716, 0x0718)


def handshake_frames(framer: MdiFramer | None = None) -> list[tuple[Step, bytes]]:
    """(step, wire-bytes) for the fixed connect handshake."""
    framer = framer or MdiFramer()
    return [(s, framer.encode(s.message())) for s in HANDSHAKE]


def handshake_is_cleared() -> bool:
    """Every handshake frame clears the parity send-gate (so connect can run for
    real once a device is attached) -- no unproven byte is ever sent."""
    framer = MdiFramer()
    return all(parity.frame_cleared(framer.encode(s.message()), s.cmd) for s in HANDSHAKE)


# ---- offline byte-proof against a recorded capture --------------------------
@dataclass
class CaptureFrame:
    offset: int
    cmd: int
    payload: bytes
    wire: bytes
    is_write: bool


def load_capture(path: str) -> list[CaptureFrame]:
    """Load a capture JSON (reference_frames_full.json shape) and rebuild each
    recorded on-wire frame from its fields. Tolerates the UTF-8 BOM."""
    with open(path, encoding='utf-8-sig') as f:
        doc = json.load(f)
    out = []
    for r in doc['frames']:
        cmd = int(r['cmd'], 16) if isinstance(r['cmd'], str) else int(r['cmd'])
        payload = bytes.fromhex((r.get('payload') or '').replace(' ', ''))
        length = int(r['len'])
        cksum = int(r['checksum'], 16) if isinstance(r['checksum'], str) else int(r['checksum'])
        wire = bytes([0xAA]) + length.to_bytes(2, 'big') + cmd.to_bytes(2, 'big') + payload + bytes([cksum])
        out.append(CaptureFrame(int(r['offset']), cmd, payload, wire, bool(r.get('is_write'))))
    return out


@dataclass
class ProofResult:
    total: int
    matched: int
    mismatches: list            # (offset, cmd, recorded_hex, ours_hex)
    reads: int
    writes: int

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.matched == self.total


def verify_capture(path: str, framer: MdiFramer | None = None) -> ProofResult:
    """Byte-prove our framer against a recorded capture of the official app:
    for every recorded outbound frame, re-encode (cmd, payload) through our own
    MdiFramer and require the bytes to be identical -- checksum included."""
    framer = framer or MdiFramer()
    frames = load_capture(path)
    matched = 0
    mism = []
    reads = writes = 0
    for fr in frames:
        writes += fr.is_write
        reads += (not fr.is_write)
        ours = framer.encode(MdiMessage(cmd=fr.cmd, data=fr.payload))
        if ours == fr.wire:
            matched += 1
        else:
            mism.append((fr.offset, fr.cmd, fr.wire.hex(), ours.hex()))
    return ProofResult(len(frames), matched, mism, reads, writes)


def decode_reply(wire: bytes, framer: MdiFramer | None = None) -> list[Frame]:
    """Feed device-reply bytes through our framer/decoder. Used offline only
    against synthetic replies (the project captures carry no real RX bytes)."""
    framer = framer or MdiFramer()
    return framer.feed(wire)


# ---- live read-only connect (2026-10-22 capture-accurate) -------------------
# The exact sequence the official app runs on connect, pinned by the vehicle
# capture. EVERY frame below is a read/handshake and clears the parity gate; no
# write is ever sent. The routine switches the UART up to 230400 after SetBaud is
# acked (as the app does) and reads the device back.

POWERON_BAUD = 115200          # FTDI opens here (capture: divisor 0x001a)
# ZMODEM cancel burst the app sends first, to kick the device out of any
# boot/zmodem state before MDI. Raw line bytes (8x CAN + 10x BS), NOT an MDI
# command, so it is not a device write and carries no parity frame.
CONNECT_ABORT_SEQ = bytes([0x18] * 8 + [0x08] * 10)
SET_BAUD_PAYLOAD = bytes.fromhex('00038400')   # 230400


@dataclass
class ConnectResult:
    product_id: int | None = None
    app_version: str | None = None
    channel_gain: dict = None          # output channel -> dB (device read-back)
    info: dict = None                  # strings parsed from the boot log
    log_lines: list = None
    frames_sent: int = 0
    replies: int = 0
    model: object = None               # model.DspModel read from the device (devread)
    capabilities: int = 0              # session capability bits (exe sess+0x38) for this product
    local_id: int | None = None        # MOST units only (LocalIDRead, capability bit 0x2)
    serial: str | None = None          # 0x0020 reply: 8 raw serial bytes (hex)
    vehicle_ini: bytes | None = None   # temp.ini the device ZMODEM-sends after the 0x0282 ack
    mode: object = None                # modes.DeviceMode the unit answered in (MDI unless detected otherwise)
    recovery: bool = False             # recovery connect: identify only (P+0xdfcb9 set), see run_connect

    def __post_init__(self):
        if self.channel_gain is None:
            self.channel_gain = {}
        if self.info is None:
            self.info = {}
        if self.log_lines is None:
            self.log_lines = []

    def as_values(self) -> dict:
        """Flatten into the dict engine.sync_from_device expects."""
        v = {'product_id': self.product_id, 'app_version': self.app_version}
        for ch, db in self.channel_gain.items():
            v['channel_gain.%d' % ch] = db
        return v


def _exchange(transport, framer, frame, expect_cmd, timeout, on_frame, rest=None):
    """Write a (parity-cleared) frame and wait for the matching MDI reply, routing
    any other frames to on_frame. Returns the reply MdiMessage or None on timeout."""
    import time
    parity.assert_frame_cleared(frame, expect_cmd, 'connect read 0x%04x' % expect_cmd)
    transport.write(frame)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frames = framer.feed(transport.read(4096, 0.05))
        for k, fr in enumerate(frames):
            if fr.kind == 'mdi' and fr.message and fr.message.cmd in (expect_cmd, 0x0FFF):
                if rest is not None:     # bytes after the reply in the same read (e.g. ZMODEM)
                    rest += b''.join(f.data for f in frames[k + 1:]) + bytes(getattr(framer, '_buf', b''))
                return fr.message            # 0x0fff: the exe's state advances (TRANSPORT §4.3)
            on_frame(fr)
    return None


# LocalIDRead / LocalIDWrite (0x0100 / 0x0101, "MostBypass"). The exe carries both
# jobs, gated on bit 0x2 of the per-job mask that the queue-command 5/6 pusher passes,
# but nothing in the app ever enqueues them, and no product row sets a bit for them
# (re/analysis/PRODUCT_TABLE.md). We mirror that exactly: the builders and the write
# helper are here, and nothing in the connect or the UI calls them.
LOCALID_CAP = 0x2                      # LocalIDRead/Write (MOST units): CMD_TABLE §1.4


def session_capabilities(product_id: int | None) -> int:
    """The per-job mask the exe's connect passes: no product enables LocalID."""
    return 0


def local_id_frames(node_id: int | None = None) -> list[tuple[int, bytes]]:
    """The exe's LocalIDRead (and, with node_id, LocalIDWrite) payloads, §1.4."""
    from . import cmdspec
    out = [(0x0100, cmdspec.localid_read())]
    if node_id is not None:
        out.append((0x0101, cmdspec.localid_write(node_id)))
    return out


def write_local_id(client, node_id: int, capabilities: int) -> bool:
    """LocalIDWrite exactly as the exe allows it: only with capability bit 0x2
    (MOST units). Returns False (nothing sent) when the product lacks the bit."""
    if not capabilities & LOCALID_CAP:
        return False
    from .message import MdiMessage
    for cmd, payload in local_id_frames(node_id):
        client.send_frame(client.framer.encode(MdiMessage(cmd=cmd, data=payload)))
    return True


class _Tap:
    """Keeps the bytes read during the MDI identify, so a unit that never answers
    MDI can be matched to the link it does speak (linkmodes.detect_mode)."""

    LIMIT = 4096

    def __init__(self, t):
        self.t, self.seen = t, bytearray()

    def read(self, n=4096, timeout=None):
        b = self.t.read(n, timeout)
        if b and len(self.seen) < self.LIMIT:
            self.seen += b[:self.LIMIT - len(self.seen)]
        return b

    def write(self, b):
        return self.t.write(b)

    def __getattr__(self, k):
        return getattr(self.t, k)


class _Prefixed:
    """A transport whose first reads return bytes already pulled off the port."""

    def __init__(self, t, head: bytes):
        self.t, self.head = t, bytearray(head)

    def read(self, n=4096, timeout=None):
        if self.head:
            out = bytes(self.head[:n]); del self.head[:n]
            return out
        return self.t.read(n, timeout)

    def write(self, b):
        return self.t.write(b)

    def __getattr__(self, k):
        return getattr(self.t, k)


def run_connect(transport, framer: MdiFramer | None = None, *, log=None,
                channels=None, read_timeout: float | None = None,
                settle: float = 0.05, capabilities: int | None = None,
                zmodem_timeout: float = 3.0, read_mask: int | None = None,
                recovery: bool = False) -> ConnectResult:
    """Run the proven read-only connect against an OPEN transport (opened at
    POWERON_BAUD) and read the device back. Reads only -- every MDI frame clears
    the parity gate; a write frame would raise before a byte left. Testable
    headless with a fake transport.

    recovery=True is the exe's reconnect with P+0xdfcb9 set (startRecovery): FUN_140099030
    then skips the product table and the DspRead, so only steps 1-3 (cancel, keep-alive,
    baud, identify) run and the result is the Recovery state (5). A unit sitting in its
    MDI bootloader (0x202c, BOOTLOADER_DISASM_OCT23.md §5) is flashed from there with the
    ordinary 0x0201 job (flashflow.run_flash)."""
    import time
    from .modes import DeviceMode
    framer = framer or MdiFramer()
    log = log or (lambda s: None)
    res = ConnectResult()
    transport = tap = _Tap(transport)
    sent = [0]
    mdi_seen = [False]       # any MDI frame at all from the unit: it is an MDI unit, full stop

    def note(s):
        log(s)

    def route(fr):
        if fr.kind == 'mdi':
            mdi_seen[0] = True
        # Boot-log lines arrive as 0x0401 debug text; collect them for device info.
        if fr.kind == 'mdi' and fr.message and fr.message.cmd == 0x0401:
            try:
                res.log_lines.append(fr.message.data.decode('latin-1').rstrip('\0'))
            except Exception:
                pass

    def send(step):
        frame = framer.encode(step.message())
        parity.assert_frame_cleared(frame, step.cmd, 'connect %s' % step.name)
        transport.write(frame)
        sent[0] += 1

    # Job timeouts from the exe's MDI table (JOB_TABLE_AND_FLASH §1.3); a test may
    # pass read_timeout to shorten every wait.
    def tmo(ms):
        return read_timeout if read_timeout is not None else ms / 1000.0

    def ask(cmd, payload=b'', ms=2000, rest=None):
        rep = _exchange(framer=framer, transport=transport,
                        frame=framer.encode(MdiMessage(cmd=cmd, data=payload)),
                        expect_cmd=cmd, timeout=tmo(ms), on_frame=route, rest=rest)
        sent[0] += 1
        if rep is not None:
            mdi_seen[0] = True
        return rep if rep is not None and rep.cmd == cmd else None

    def set_baud():
        # FUN_14016fdb0: switch the host only on a 0x0002 reply with status 01
        rep = ask(0x0002, SET_BAUD_PAYLOAD, 500)
        if rep is not None and rep.data[:1] == b'\x01':
            time.sleep(settle)
            try:
                transport.set_baud(CONNECT_BAUD)
                note('connect: UART switched to %d baud' % CONNECT_BAUD)
            except NotImplementedError:
                pass
            return True
        return False

    # 1) raw ZMODEM cancel, then clear anything buffered
    transport.write(CONNECT_ABORT_SEQ)
    transport.flush_input()
    note('connect: sent ZMODEM cancel, negotiating at %d baud' % POWERON_BAUD)

    # 2) the captured order: 0000 0000 0002 [switch] 0000 0000 0002
    send(Step('keepalive', 0x0000, b''))
    send(Step('keepalive', 0x0000, b''))
    set_baud()
    send(Step('keepalive', 0x0000, b''))
    send(Step('keepalive', 0x0000, b''))
    set_baud()

    # 3) identify: product id + app version from the 0x0001 reply
    def identify(rep):
        if rep is None or len(rep.data) < 8:
            return False
        res.product_id = int.from_bytes(rep.data[0:2], 'big')
        if len(rep.data) >= 12:
            res.app_version = '%d.%d.%d' % (rep.data[9], rep.data[10], rep.data[11])
        note('connect: product 0x%04x, app %s' % (res.product_id, res.app_version))
        return True

    identify(ask(0x0001, b'', 2000))
    res.mode = DeviceMode.MDI
    if recovery:
        # P+0xdfcb9: the exe stops after the identify. Never the session open, the
        # ConfigSync 0x0282 or the DspRead, whatever the unit answered; a unit that
        # sent no MDI frame is matched to its own link from the bytes seen so far.
        res.recovery = True
        if not mdi_seen[0]:
            from . import linkmodes
            other = linkmodes.detect_mode(bytes(tap.seen))
            if other is not DeviceMode.UNKNOWN:
                res.mode = other
                note('connect: device is in %s mode, not MDI' % other.name)
        res.frames_sent = sent[0]
        note('connect: recovery mode, identify only')
        return res

    # 4) 0020 0025 0400 0001 0282 (SerialNumber, DipConfig, EnableDebug, Ping, ConfigSync)
    for step, ms in ((Step('session_open', 0x0020), 2000), (Step('handshake', 0x0025), 2000),
                     (Step('stream_on', 0x0400, b'\x01'), 5000), (Step('ping', 0x0001), 1000),
                     (Step('get_config', 0x0282), 5000)):
        rest = bytearray()
        rep = ask(step.cmd, step.payload, ms, rest)
        if step.cmd == 0x0001 and res.product_id is None:
            identify(rep)            # a slow unit / lost first frame: the ping is the same request
        if step.cmd == 0x0020 and rep and len(rep.data) == 8:
            res.serial = rep.data.hex()
        if step.cmd == 0x0282 and rep and rep.data[:1] == b'\x01':
            # The device now ZMODEM-sends temp.ini on the same port, and its byte
            # router feeds every incoming byte to ZMODEM until that ends
            # (FW_MDI_HANDLERS_OCT22). Receive it before any further MDI frame.
            from . import zmodem
            try:
                name, data = zmodem.receive_file(_Prefixed(transport, bytes(rest)),
                                                 timeout=zmodem_timeout, max_size=1 << 20)
                res.vehicle_ini = data
                note('connect: received %s (%d bytes)' % (name, len(data)))
            except TimeoutError:
                note('connect: no vehicle file from the device')
            except zmodem.ZmodemError as e:
                note('connect: vehicle file transfer failed: %s' % e)
                zmodem.abort(transport)
            framer.reset()

    # 4b) Another link mode (UNKNOWNM sub-modes, linkmodes.py) only after the whole
    #     MDI open above got no MDI frame at all, and only on that link's own bytes
    #     (U+magic token, a checksummed FF 55 frame, a BOOTS console line). A slow or
    #     busy MDI unit, or one that lost a frame, never gets here: any MDI frame
    #     from it keeps the MDI path, and silence alone is not evidence.
    if not mdi_seen[0]:
        from . import linkmodes
        other = linkmodes.detect_mode(bytes(tap.seen))
        if other is not DeviceMode.UNKNOWN:
            res.mode = other
            if other is DeviceMode.LW:
                ident_lw = linkmodes.lw_identify(transport, timeout=tmo(1000), log=note)
                if ident_lw is not None:
                    res.product_id, res.app_version = ident_lw.product_id, ident_lw.version
            note('connect: device is in %s mode, not MDI' % other.name)
            res.frames_sent = sent[0]
            return res

    # 5) the official DspRead: the whole tune into a DspModel (devread.read_tune)
    #    in the exe's read order. Reads only.
    from . import devread
    from . import model as _model

    def exchange(cmd, payload):
        return _exchange(framer=framer, transport=transport,
                         frame=framer.encode(MdiMessage(cmd=cmd, data=payload)),
                         expect_cmd=cmd, timeout=tmo(2000), on_frame=route)

    res.capabilities = session_capabilities(res.product_id) if capabilities is None else capabilities

    root, n = devread.read_tune(exchange, _model.DspModel(), product_id=res.product_id, log=note,
                                 read_mask=read_mask)
    sent[0] += n
    res.model = root
    # channel gains as older callers expect them (0 = master, ch+1 = output channel)
    pr = root.presets[root.current_preset]
    res.channel_gain[0] = pr.master.gain_db
    for ch in root.channels():
        res.channel_gain[ch + 1] = root.speaker(root.channel_role[ch], root.current_preset).gain_db
    res.replies = len(res.channel_gain)
    note('connect: fw %d, %d preset(s), %d mapped channel(s)'
         % (root.fw, root.preset_count, len(root.channels())))

    # device info strings out of the boot log
    import re as _re
    rx = _re.compile(r'([A-Za-z][\w .]+?)\s*[:=]\s*(.+)')
    for ln in res.log_lines:
        m = rx.search(ln)
        if m:
            res.info.setdefault(m.group(1).strip(), m.group(2).strip())
    res.frames_sent = sent[0]
    note('connect: %d frames sent, %d channel value(s) read' % (res.frames_sent, len(res.channel_gain)))
    return res


def _gain_db_from_raw(raw_be16: int) -> float:
    """Decode a 0x0710 gain read with the live-gated unity (set by DSPNV version).
    encoders._gain_unity is a signed 16-bit value; mask to the unsigned wire form."""
    from . import encoders
    return (raw_be16 - (encoders._gain_unity & 0xFFFF)) / 512.0
