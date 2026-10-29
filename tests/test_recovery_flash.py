"""Recovery identify/exit (both languages) and the BOOTS flash job, against simulated
bootloaders. Every byte the host writes is checked against the exe-derived sequence
(boots.py docstring), including baud changes."""
import struct

import pytest

from mobridge import boots, fif, flashflow as F, uirules as R
from mobridge.commands import Access
from mobridge.safety import Policy
from mobridge.transport import Transport
from test_offline import make_fif

MAGIC_LINE = b'U' + struct.pack('<I', boots.LW_MAGIC)          # 55 bb 45 2e 7c
WRITE = Policy.allow(Access.WRITE)


class SimBoots(Transport):
    """BF52x-style bootloader: text console + LW knock + raw `flash -uart` receiver.
    Records (baud, bytes) for every write."""

    def __init__(self, product=0x202c, ver=(2, 11, 9), text=True, lw=True, fail=False):
        self.product, self.ver, self.text, self.lw, self.fail = product, ver, text, lw, fail
        self.baud = 115200
        self.log = []                      # ('w', baud, bytes) / ('baud', rate)
        self.out = bytearray()
        self.rx = bytearray()
        self.receiving = None              # expected byte count once READY was sent
        self.image = bytearray()

    def set_baud(self, baud):
        self.baud = baud
        self.log.append(('baud', baud))

    def flush_input(self):
        self.out.clear()

    def write(self, data):
        self.log.append(('w', self.baud, bytes(data)))
        if self.receiving is not None and len(self.image) < self.receiving:
            self.image += data
            if len(self.image) >= self.receiving:
                self.out += b'OPENING\r\nMD5 CHECK\r\nPROGRAMMING\r\n'
                self.out += b'ERROR: md5\r\n' if self.fail else b'PROGRESS 50\r\nPROGRESS 100\r\n'
            return
        self.rx += data
        while True:
            if self.lw and self.rx.startswith(b'\x00'):
                del self.rx[0]; continue
            if self.lw and self.rx.startswith(boots.LW_KNOCK):
                del self.rx[:5]; self.out += MAGIC_LINE; continue
            if self.lw and self.rx.startswith(b'i'):
                del self.rx[0]
                self.out += b'I' + struct.pack('<BHBBB', 1, self.product, *self.ver); continue
            if self.lw and self.rx.startswith(b'x'):
                del self.rx[0]; self.out += b'X'; continue
            if self.rx.startswith(boots.CANCEL_BURST):
                del self.rx[:18]; continue
            nl = self.rx.find(b'\n')
            if nl < 0:
                break
            line = bytes(self.rx[:nl]).strip(); del self.rx[:nl + 1]
            if not self.text or not line:
                continue
            if line == b'echo off':
                self.out += b'OK\r\n'
            elif line == b'version':
                self.out += b'Prod=0x%x, Ver=%d.%d.%d\r\n' % ((self.product,) + self.ver)
            elif line == b'ping':
                self.out += b'OK\r\n'
            elif line.startswith(b'baud '):
                pass
            elif line == b'flash -uart':
                self.out += b'READY\r\n'
                self.receiving = self.expect

    def read(self, size=4096, timeout=0.1):
        d = bytes(self.out[:size]); del self.out[:size]
        return d


def _img(tmp_path, name='f.fif', **kw):
    p = tmp_path / name; p.write_bytes(make_fif(**kw)); return str(p)


def _writes(dev):
    return [e for e in dev.log if e[0] == 'w']


def test_token_parser():
    tp = boots.TokenParser()
    toks = tp.feed(b'\r\nzz' + MAGIC_LINE + b'I\x01\x2c\x20\x02\x0b')
    assert toks[0].char == 'U' and toks[0].word == 0x7c2e45bb and tp.buf == b'I\x01\x2c\x20\x02\x0b'
    toks = tp.feed(b'\x09Dhello\x00X')
    assert [(t.char, t.data) for t in toks] == [('I', b'\x01\x2c\x20\x02\x0b\x09'), ('D', b'hello'), ('X', b'')]


def test_identify_text_language():
    dev = SimBoots(lw=False)
    b = boots.BootsFlasher(dev, Policy.read_only(), log=lambda s: None)
    ident = b.identify(timeout=0.3)
    assert (ident.product, ident.version, ident.language) == (0x202c, (2, 11, 9), 'text')
    assert [w[2] for w in _writes(dev)] == [b'echo off\n', b'version\n']
    assert b.exit_recovery(ident) and len(_writes(dev)) == 2      # text exit sends nothing


def test_identify_lw_language_fallback_and_exit():
    dev = SimBoots(product=0x2033, ver=(1, 4, 2), text=False)
    b = boots.BootsFlasher(dev, Policy.read_only(), log=lambda s: None)
    ident = b.identify(timeout=0.3)
    assert (ident.product, ident.version, ident.language) == (0x2033, (1, 4, 2), 'lw')
    assert b.exit_recovery(ident)
    w = [x[2] for x in _writes(dev)]
    assert w == [b'echo off\n', b'\x00' * 66 + bytes.fromhex('75de233da1'), b'i', b'x']


def test_boots_flash_wire_sequence(tmp_path):
    path = _img(tmp_path, product=0x2046, body=bytes(range(256)) * 10)   # 2560-byte body
    data = open(path, 'rb').read()
    dev = SimBoots(product=0x2046); dev.expect = len(data)
    st = boots.BootsFlasher(dev, WRITE, log=lambda s: None).flash(path, execute=True, line_timeout=0.5)
    assert st.state == 'complete' and bytes(dev.image) == data       # raw file bytes, no ZMODEM
    seq = [(e[0], e[1], e[2]) if e[0] == 'w' else e for e in dev.log]
    head = [b'echo off\n', b'version\n']
    assert [s[2] for s in seq[:2]] == head
    rest = seq[2:]
    assert rest[:9] == [
        ('w', 115200, b'\necho off\n'), ('baud', 19200), ('baud', 115200),
        ('w', 115200, boots.CANCEL_BURST),
        ('w', 115200, b'\x00' * 66 + b'u\xde\x23\x3d\xa1'),
        ('w', 115200, b'baud 230400\n'), ('baud', 230400),
        ('w', 230400, b'ping\n'), ('w', 230400, b'\n\n\nflash -uart\n')]
    chunks = [e[2] for e in rest[9:-1]]
    assert all(len(c) <= 0x400 for c in chunks) and b''.join(chunks) == data
    assert rest[-1] == ('w', 230400, boots.CANCEL_BURST)


def test_boots_flash_error_still_sends_exit(tmp_path):
    path = _img(tmp_path, product=0x2046)
    dev = SimBoots(product=0x2046, fail=True); dev.expect = len(open(path, 'rb').read())
    st = boots.BootsFlasher(dev, WRITE, log=lambda s: None).flash(path, execute=True, line_timeout=0.5)
    assert st.state == 'error' and dev.log[-1][2] == boots.CANCEL_BURST


def test_boots_refuses_wrong_product_after_identify(tmp_path):
    path = _img(tmp_path, product=0x2046)
    dev = SimBoots(product=0x2019); dev.expect = 0
    with pytest.raises(ValueError, match='not flashing'):
        boots.BootsFlasher(dev, WRITE, log=lambda s: None).flash(path, execute=True, line_timeout=0.3)
    assert [w[2] for w in _writes(dev)] == [b'echo off\n', b'version\n']   # nothing past identify


def test_202c_never_takes_the_boots_path(tmp_path):
    """0x202c's bootloader has no flash -uart (BOOTLOADER_DISASM_OCT23.md §5): refuse before
    any byte for a 0x202c image, and after identify for a wildcard image on a 0x202c unit."""
    dev = SimBoots(); dev.expect = 0
    b = boots.BootsFlasher(dev, WRITE, log=lambda s: None)
    for ex in (False, True):
        with pytest.raises(ValueError, match='MDI 0x0201'):
            b.flash(_img(tmp_path), execute=ex, line_timeout=0.3)
    assert _writes(dev) == []
    with pytest.raises(ValueError, match='MDI 0x0201'):
        b.flash(_img(tmp_path, 'w.fif', product=0), execute=True, line_timeout=0.3)
    assert [w[2] for w in _writes(dev)] == [b'echo off\n', b'version\n']


def test_202c_recovery_bytes_are_pinned():
    """What the 0x202c bootloader accepts (§5): MDI frames, not BOOTS text. The app's own
    MDI framer and flash start must produce exactly these bytes."""
    from mobridge.framing import MdiFramer
    from mobridge.message import MdiMessage
    enc = lambda c: MdiFramer().encode(MdiMessage(cmd=c, data=b''))
    assert enc(0x0000) == boots.MDI_PROBE
    assert enc(0x0001) == boots.MDI_IDENTIFY
    assert enc(0x0201) == boots.MDI_FLASH_START
    assert boots.MDI_FLASH_START_REPLY == b'\x01'
    for b in (boots.MDI_PROBE, boots.MDI_IDENTIFY, boots.MDI_FLASH_START):
        assert b'flash' not in b and b'echo' not in b


def test_most_factory_image_flashes_in_recovery(tmp_path):
    """Stock MOST 2.0.0 layout: 0x48 preamble, product 0x2019, offset 0x0 (rewrites the bootloader)."""
    path = _img(tmp_path, product=0x2019, version=0x00020000, flash_off=0, pre_len=0x48)
    data = open(path, 'rb').read()
    dev = SimBoots(product=0x2019, ver=(1, 0, 0)); dev.expect = len(data)
    st = boots.BootsFlasher(dev, WRITE, log=lambda s: None).flash(path, execute=True, line_timeout=0.5)
    assert st.state == 'complete' and bytes(dev.image) == data


def test_bootblock_guard_per_family(tmp_path):
    most = fif.parse(make_fif(product=0x2019, flash_off=0))
    ford = fif.parse(make_fif(product=0x202c, flash_off=0))
    wild = fif.parse(make_fif(product=0, flash_off=0))
    assert fif.flash_problems(most, 0x2019) == [] and fif.bootblock_problem(most) is None
    assert any('bootblock' in p for p in fif.flash_problems(ford, 0x202c))
    assert fif.bootblock_problem(wild, 0x2024) is None and fif.bootblock_problem(wild, 0x202c)
    assert fif.bootblock_problem(fif.parse(make_fif(flash_off=0x60000))) is None
    m = F.validate(_img(tmp_path, 'm.fif', product=0x2019, version=0x00020000, flash_off=0), 0x2019, (1, 0, 0))
    assert m.code == F.OK and not m.guard
    g = F.validate(_img(tmp_path, 'g.fif', product=0x2046, flash_off=0), 0x2046, (2, 11, 0))
    assert g.code == F.OK and g.guard                    # A2B GM: factory images never touch 0x0
    assert boots.BootsFlasher.check(_img(tmp_path, 'g2.fif', product=0x2046, flash_off=0), 0x2046,
                                    (2, 11, 0)) and R.STATE_RECOVERY == 5
