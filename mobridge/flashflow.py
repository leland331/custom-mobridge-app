"""Firmware update flow before and after the transfer, as the exe runs it
(UI_REGION_2.md §5: FUN_1400a2870 -> FUN_1400ab740 -> FUN_14009d7c0 / FUN_14009dd70,
FUN_1400af1c0 case 0, FUN_140099030).

1. validate: open/parse (6), product id == device or 0 (4), NVM_FIXED (5),
   DSPNV_VERS > 210 (2), device < MIN_PREV_VERS (3), fif <= device outside
   Recovery (1), else 0.
2. messages / prompts for each code.
3. confirm "about to be updated from %1 to %2" (or "recovered to %1"), Ok|Cancel.
4. automatic .rax backup backup_<pid>_<devname>_<dd-MM-yy_hh-mm-ss>.rax.
5. write_back = fif DSPNV_VERS != device settings version: after the post-flash
   re-read the previous model is kept and written back whole.
Our one addition is fif.bootblock_problem: StartAddr < 0x60000 is refused except for the
families whose factory images rewrite the bootloader (fif.BOOTBLOCK_FAMILIES)."""
from __future__ import annotations

import collections
import os
import time
from dataclasses import dataclass

from . import fif, uirules as R

OK, OLDER, INCOMPATIBLE_APP, MIN_PREV, INCOMPATIBLE_DEVICE, NVM_FIXED, INVALID = range(7)


def _ver3(s) -> tuple:
    try:
        a = [int(x) for x in str(s).split('.')]
        return tuple((a + [0, 0, 0])[:3])
    except ValueError:
        return (0, 0, 0)


def fif_ver(info: fif.FifInfo) -> tuple:
    v = info.version
    return (v >> 16 & 0xFF, v >> 8 & 0xFF, v & 0xFF)


@dataclass
class Plan:
    path: str
    info: fif.FifInfo | None
    code: int
    min_prev: tuple = (0, 0, 0)
    guard: list | None = None          # bootloader-guard reasons (our addition)

    @property
    def dspnv(self) -> int:
        return int((self.info.preamble_json or {}).get('DSPNV_VERS', 0)) if self.info else 0


def validate(path: str, device_product: int, device_ver: tuple, state: int = R.STATE_CONNECTED) -> Plan:
    """FUN_1400ab740."""
    try:
        info, _ = fif.load(path)
    except (OSError, fif.FifError):
        return Plan(path, None, INVALID)
    if not info.intact:
        return Plan(path, info, INVALID)
    pj = info.preamble_json or {}
    min_prev = _ver3(pj.get('MIN_PREV_VERS', '0.0.0'))
    code = R.validate_firmware(info.product, device_product, int(pj.get('DSPNV_VERS', 0)), min_prev,
                               tuple(device_ver), fif_ver(info), bool(pj.get('NVM_FIXED', False)), state)
    bb = fif.bootblock_problem(info, device_product)
    guard = [bb] if bb else []
    return Plan(path, info, code, min_prev, guard)


def message(plan: Plan) -> str:
    """FUN_14009d7c0 text for a failing code (codes 1, 3, 5 are prompts, see prompt())."""
    if plan.code == INCOMPATIBLE_APP:
        return R.FW_VALIDATE[2].replace('mObridge DSP', R.APP_NAME)
    if plan.code == MIN_PREV:
        return R.FW_VALIDATE[3].replace('%1', os.path.basename(plan.path)).replace(
            '%2', '%d.%d.%d' % plan.min_prev)
    return R.FW_VALIDATE.get(plan.code, R.FW_VALIDATE[6])


def find_min_prev(dirs, want: tuple) -> str | None:
    """FUN_1400a09b0 / FUN_1400a1fa0: a .fif whose version equals MIN_PREV_VERS."""
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.lower().endswith('.fif'):
                p = os.path.join(d, n)
                try:
                    info, _ = fif.load(p)
                except (OSError, fif.FifError):
                    continue
                if fif_ver(info) == tuple(want):
                    return p
    return None


def confirm_text(plan: Plan, device_ver: tuple, state: int = R.STATE_CONNECTED) -> str:
    """FUN_14009dd70 Ok|Cancel warning text."""
    new = '%d.%d.%d' % fif_ver(plan.info)
    if state == R.STATE_RECOVERY:
        head = R.FW_CONFIRM['recovery'].replace('%1', new)
    else:
        head = R.FW_CONFIRM['update'].replace('%1', '%d.%d.%d' % tuple(device_ver)).replace('%2', new)
    return head + '<br><br>' + R.FW_CONFIRM['tail']


def backup_path(folder: str, product_id: int, device_name: str, now: float | None = None) -> str:
    stamp = time.strftime('%d-%m-%y_%H-%M-%S', time.localtime(now))
    return os.path.join(folder, R.backup_file_name(product_id, device_name, stamp))


def write_back_needed(plan: Plan, device_settings_ver: int) -> bool:
    """FUN_1400af1c0 case 0: P+0xdfcba = fif DSPNV_VERS != settings version."""
    return plan.dspnv != device_settings_ver


def mark_full_write(root) -> None:
    """vtable +0x48 (P, 0, 1): every value of the kept model goes back to the device."""
    from . import model as M
    root.root_flags |= (M.ROOT_NAME | M.ROOT_INPUT_MAP | M.ROOT_INPUT_GAINS | M.ROOT_INPUT_DELAY
                        | M.ROOT_SOURCESEL | M.ROOT_OPTIONS)
    for pr in root.presets:
        pr.master.flags |= M.MASTER_GAIN | M.MASTER_BINS | M.MASTER_EQ
        for s in pr.speakers.values():
            s.mark_all()


# --- the FlashFile job (FLASH_GROUP_C.md C1) ---------------------------------
START_S, SEND_S, WAIT_S = 2.0, 300.0, 300.0
START_TRIES = 3
# After ZEOF the device leaves ZMODEM and its ~5 s idle timer (bootloader 0xffa0aab4, paused
# only during ZMODEM) drops the link to 115200 unless a valid MDI frame arrives: so ZFIN gets
# a short wait only, and 0x0000 goes out on a timer while flashing (erase gaps send nothing).
FIN_WAIT_S, KEEPALIVE_S = 1.0, 2.0
# 0x0200 sub -> FLASH_STATUS key (FUN_14017a8c0 phase -> FUN_14004f560 strings)
SUB_STATUS = {0: 4, 1: 5, 2: 6, 3: 7, 4: 8, 5: 8, 10: 9, 11: 9, 12: 9}


def run_flash(session, data: bytes, name: str, status=None) -> bool:
    """Start (bare 0x0201, 2 s) -> SendFile (raw ZMODEM; the device never answers ZFIN, so
    no wait for it) -> WaitComplete (0x0200 progress; 0x0000 keep-alive per frame and every
    KEEPALIVE_S, 300 s) -> sub 8 finished / first sub 9 error. Sub 8 means finished, not
    succeeded (a product mismatch or verify failure also ends in sub 8): the caller resets
    (0x0010; neither firmware reboots by itself) and re-reads the version."""
    from . import zmodem
    status = status or (lambda kind, a=0, b=0: None)
    size, pos = len(data), 0

    def on_progress(m) -> int | None:
        nonlocal size, pos
        if len(m.data) < 6:
            return None
        sub = int.from_bytes(m.data[:2], 'big')
        arg = int.from_bytes(m.data[2:6], 'big', signed=True)
        if sub == 6:
            size = arg
        elif sub == 7:
            pos = arg
            status('position', pos, size)
        elif sub == 8:
            return 1
        elif sub == 9:
            return -1
        elif sub in SUB_STATUS:
            status('phase', SUB_STATUS[sub])
        return None

    status('phase', 1)                                  # Upload Starting
    for _ in range(START_TRIES):
        session._send(0x0201, b'')
        rep = session._pump(START_S, want=0x0201)
        if rep is not None and rep.cmd == 0x0201:
            break
    else:
        status('phase', 3)                              # Upload Error
        return False
    session.framer.reset()
    status('phase', 2)                                  # Uploading...
    extra = bytearray()
    try:
        zmodem.send_file(session.t, data, name, timeout=SEND_S, fin_wait=FIN_WAIT_S,
                         on_extra=extra.extend,
                         progress=lambda done, total: status('upload', done, total))
    except Exception:
        status('phase', 3)
        return False
    session.framer.reset()
    if extra:                                           # MDI frames read during the ZFIN wait
        session.__dict__.setdefault('_backlog', collections.deque()).extend(
            session.framer.feed(bytes(extra)))
    session._send(0x0000, b'')
    last_ka = time.monotonic()
    deadline = last_ka + WAIT_S
    while time.monotonic() < deadline:
        wait = max(0.0, min(last_ka + KEEPALIVE_S, deadline) - time.monotonic())
        m = session._pump(wait, want=0x0200)
        if m is not None or time.monotonic() - last_ka >= KEEPALIVE_S:
            session._send(0x0000, b'')                  # per event (as the exe) and on the timer
            last_ka = time.monotonic()
        if m is None or m.cmd != 0x0200:
            continue
        r = on_progress(m)
        if r is not None:
            status('phase', 10 if r > 0 else 11)        # Complete! / Error!
            return r > 0
    status('phase', 11)
    return False
