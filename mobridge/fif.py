""".fif firmware image checks, done before anything is sent to a device.

Layout (big-endian, offsets relative to the magic, which sits after a NUL-padded
text preamble: 0x48 in the 2.0.0 stock files, 0x60 in 2.11.x):

  +0x00 magic d38ba62c | +0x04 format | +0x08 count | +0x0c 16 reserved
  +0x1c md5(entry)     | +0x2c entry (32 B): md5(inflated body), product id,
  version, flash offset, inflated size | +0x4c zlib body

Building/editing images is done by the firmware tools, not this app;
this module only validates and describes them.
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from dataclasses import dataclass

FIF_MAGIC = b'\xd3\x8b\xa6\x2c'
BOOTBLOCK_END = 0x60000          # bootloader lives in SPI flash 0x0-0x60000
FLASH_SIZE = 0x400000            # 4 MiB on the reference DA-G2.Pro A2B Ford
PRODUCT_DA_G2_PRO_A2B_FORD = 0x202C


@dataclass
class FifInfo:
    path: str
    magic_offset: int
    preamble: str
    format: int
    count: int
    header_md5_ok: bool
    body_md5_ok: bool
    size_ok: bool
    product: int
    version: int
    flash_offset: int
    size: int
    body_md5: str
    preamble_json: dict | None

    @property
    def version_str(self) -> str:
        v = self.version
        return '%d.%d.%d' % (v >> 16, v >> 8 & 0xFF, v & 0xFF)

    @property
    def intact(self) -> bool:
        return self.header_md5_ok and self.body_md5_ok and self.size_ok


class FifError(ValueError):
    pass


def parse(data: bytes, path: str = '<bytes>') -> FifInfo:
    m = data.find(FIF_MAGIC, 0, 0x200)
    if m < 0:
        raise FifError('no .fif magic d38ba62c in the first 512 bytes')
    fmt, count = struct.unpack_from('>II', data, m + 4)
    hdr_md5 = data[m + 0x1C:m + 0x2C]
    entry = data[m + 0x2C:m + 0x4C]
    if len(entry) < 32:
        raise FifError('file truncated inside the header')
    body_md5 = entry[:16]
    product, version, flash_off, size = struct.unpack('>IIII', entry[16:])
    try:
        body = zlib.decompress(data[m + 0x4C:])
    except zlib.error as e:
        raise FifError('zlib body does not inflate: %s' % e)
    pre = data[:m].rstrip(b'\0').decode('latin-1')
    # FUN_1400abc70: the JSON header is everything up to and including the first '}'
    end = pre.find('}')
    try:
        pj = json.loads(pre[:end + 1]) if end >= 0 else None
    except ValueError:
        pj = None
    if not isinstance(pj, dict):
        pj = None
    return FifInfo(path, m, pre, fmt, count,
                   hashlib.md5(entry).digest() == hdr_md5,
                   hashlib.md5(body).digest() == body_md5,
                   size == len(body), product, version, flash_off, size, body_md5.hex(), pj)


def load(path: str) -> tuple[FifInfo, bytes]:
    with open(path, 'rb') as f:
        data = f.read()
    return parse(data, path), data


# Products whose official factory images write from flash 0x0, i.e. rewrite the bootloader
# as well. PROVEN by parsing the four stock files da_g2_{pro,standard}_{most25,most150}_
# combined_2.0.0.fif (DEVICE_COMPAT_AUDIT.md R1: products below, offset 0x0). Every other
# family's known factory image (DA-G2 A2B, e.g. Ford 2.11.8) starts at 0x60000, so for
# them a bootloader-region image is refused as a rogue file. Not in the official app; our
# one extra check, scoped so it never blocks real factory firmware.
BOOTBLOCK_FAMILIES = {
    0x2002: 'DA-G2.Pro MOST25',
    0x2019: 'DA-G2.Pro MOST150',
    0x2024: 'DA-G2.Standard MOST25',
    0x2025: 'DA-G2.Standard MOST150',
}


def bootblock_problem(info: FifInfo, device_product: int | None = None) -> str | None:
    """Reason to refuse an image that writes into 0x0-0x60000, or None.
    Allowed when the image's product (or, for a product-0 wildcard image, the device's)
    is a family whose factory images rewrite the bootloader."""
    if info.flash_offset >= BOOTBLOCK_END:
        return None
    fam = info.product if info.product else device_product
    if fam in BOOTBLOCK_FAMILIES:
        return None
    return ('flash offset %#x is inside the bootblock (0x0-%#x): refusing, this could brick the bootloader'
            % (info.flash_offset, BOOTBLOCK_END))


def flash_problems(info: FifInfo, expect_product: int | None = PRODUCT_DA_G2_PRO_A2B_FORD) -> list[str]:
    """Reasons this image must not be flashed. Empty list = OK to proceed."""
    p = []
    if not info.header_md5_ok:
        p.append('header MD5 mismatch')
    if not info.body_md5_ok:
        p.append('body MD5 mismatch (corrupt or edited without repack)')
    if not info.size_ok:
        p.append('inflated size does not match header')
    bb = bootblock_problem(info, expect_product)
    if bb:
        p.append(bb)
    # product id 0 is the exe's wildcard (FUN_1400ab740)
    if expect_product is not None and info.product not in (expect_product, 0):
        p.append('product id %#06x does not match the device (%#06x)' % (info.product, expect_product))
    return p


def describe(info: FifInfo) -> str:
    ok = lambda b: 'OK' if b else 'BAD'
    return '\n'.join([
        'file          : %s' % info.path,
        'preamble      : %s' % info.preamble.replace('\n', ' | '),
        'magic at      : %#x  (format %d, %d image)' % (info.magic_offset, info.format, info.count),
        'header md5    : %s' % ok(info.header_md5_ok),
        'body md5      : %s  %s' % (info.body_md5, ok(info.body_md5_ok)),
        'product id    : %#06x' % info.product,
        'version       : %s' % info.version_str,
        'flash offset  : %#x' % info.flash_offset,
        'inflated size : %#x (%s)' % (info.size, ok(info.size_ok)),
    ])
