"""Chime logger: passively record the Mobridge debug log in the vehicle.

    python -m mobridge.chimelog rfc2217://192.0.2.10:4000      # from any machine
    python -m mobridge.chimelog rfc2217://localhost:4000          # on the Pi, via ser2net
    python -m mobridge.chimelog --port /dev/ttyUSB0               # on the Pi, no network (pi/chimelog.service)

What it sends: ONLY the app's existing read-only connect (connect.run_connect,
every frame checked by the parity gate) and, unless --no-keepalive, the same
0x0001 keep-alive the app's Session sends about once a second. No writes.

--enable-chime-log (the user's go, 2026-10-25): once after each connect, also type
the debug-console lines `d` then `all`, which switch on every log stream (incl.
CAN_FORD_APP, where handle_chimes prints). RAM-only, cleared at the next reboot;
run on the unit via the official app on 2026-10-21 with no harm. Bytes: see
ENABLE_FRAMES. Without the flag, no console text is ever sent.

What it records: every 0x0401 debug-log line and every 0x0646 (A2B) / 0x0615
(CAN) bus event, timestamped, to <out>.txt (human) and <out>.jsonl (one JSON
object per line). Chime lines are marked '***' and printed live.

Power loss: the vehicle can cut the Pi at any moment. Every record is flushed at
once and fsync'd at least every --sync seconds, so at most the last second is
lost and every file stays readable line by line. A dropped link (unit off, cable
pulled) is logged and the connect is retried until Ctrl+C.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

from . import busevents, connect
from .framing import MdiFramer
from .message import MdiMessage

LOG_CMD = 0x0401
CHIME_CAN_IDS = (0x220, 0x2F4)
KEEPALIVE_S = 1.0

# --enable-chime-log: exactly what the official Debug Console sends when `d`,
# Enter, `all`, Enter are typed. Sources: exe DebugSend job = CMD 0x0401,
# FUN_14017bb40 (re/analysis/CMD_TABLE.md 1.6, EXE_DEEP_RE_OCT22.md 3): one frame
# per keystroke, payload = that byte, no terminator added; Enter = 0x0d, captured
# PC->device in captures/cap2_out.hex ('h' = aa000104016892, Enter = aa000104010ded).
# Prerequisite: the 0x0400 [01] stream-on the read-only connect already sends;
# the device needs no password (NOTES.md 10/11). The tokens and order are from
# docs/fault-capture/CONSOLE_RECIPE.md (device results 2026-10-21: `d`, `all` work).
ENABLE_LINES = ('d', 'all')
ENABLE_GAP_S = 1.5          # CONSOLE_RECIPE wait after `d` before `all`
ENABLE_FRAMES = {           # aa | len 0001 | cmd 0401 | byte | 0x100 - sum(len..byte)
    'd':   ('aa000104016496', 'aa000104010ded'),
    'all': ('aa000104016199', 'aa000104016c8e', 'aa000104016c8e', 'aa000104010ded'),
}

# Lines worth calling out. handle_chimes is printed by the CAN_FORD_APP stream
# (off by default); WSNDMGR prints by default.
CHIME_RX = re.compile(r'handle_chimes|chime|WSNDMGR|wsnd|ID:\s*0x[0-9a-f]{2}', re.I)
CHIME_ID_RX = re.compile(r'handle_chimes:\s*ID:\s*0x([0-9a-f]{1,2})', re.I)
# The vehicle repeats this ~2x per second with nothing playing; record it, don't shout it.
QUIET_RX = re.compile(r'WSNDMGR_CancelWarning:\s*255')


def uptime() -> float:
    """Seconds since boot (CLOCK_BOOTTIME where it exists). The Pi has no RTC and no
    network in the vehicle, so its wall clock may be wrong; this one is not."""
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return time.monotonic()


def boot_number(state_file: str) -> int:
    """Count boots: bump the number in state_file whenever the kernel boot_id changes."""
    try:
        boot_id = open('/proc/sys/kernel/random/boot_id').read().strip()
    except OSError:
        boot_id = ''
    n, last = 0, None
    try:
        n_s, last = open(state_file).read().split()[:2]
        n = int(n_s)
    except (OSError, ValueError):
        pass
    if last != boot_id or not boot_id:
        n += 1
        tmp = state_file + '.tmp'
        with open(tmp, 'w') as f:
            f.write('%d %s\n' % (n, boot_id or '-'))
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, state_file)
    return n


def uucp_lock(port: str, lock_dir: str = '/run/lock'):
    """Take the classic UUCP lock (LCK..ttyUSB0) that ser2net checks before opening
    the port, so while we hold the serial port a network client is refused with
    "port in use" instead of both sides talking at once. Returns the lock path or
    None (not a device port / lock dir missing). Raises if someone alive holds it."""
    if not port.startswith('/dev/') or not os.path.isdir(lock_dir):
        return None
    path = os.path.join(lock_dir, 'LCK..' + os.path.basename(port))
    try:
        pid = int(open(path).read().strip())
        os.kill(pid, 0)
        if pid != os.getpid():
            raise RuntimeError('%s is in use by pid %d (ser2net client or the app?)' % (port, pid))
    except (OSError, ValueError):
        pass                                     # no lock, or a stale one
    with open(path, 'w') as f:
        f.write('%10d\n' % os.getpid())
    return path


class ChimeLog:
    """Writes records to <base>.txt and <base>.jsonl, power-loss safe."""

    def __init__(self, base: str, echo=print, sync_s: float = 1.0, clock=time.time):
        d = os.path.dirname(os.path.abspath(base))
        os.makedirs(d, exist_ok=True)
        self.txt = open(base + '.txt', 'a', encoding='utf-8', buffering=1)
        self.jsonl = open(base + '.jsonl', 'a', encoding='utf-8', buffering=1)
        self.echo = echo
        self.sync_s = sync_s
        self.clock = clock
        self._last_sync = 0.0
        self._buf = ''                      # 0x0401 text not yet ended by a newline
        self.boot = None                    # boot number (set by main on the Pi)
        self.chime_ids: dict[int, int] = {}   # id -> times seen (console or CAN)
        self.lines = 0
        self.log_lines = 0                  # 0x0401 lines from the device
        self.last_line_t = None
        self.last_chime = None
        self.status_path = None             # set by main: JSON status, rewritten every status_s
        self.status_s = 3.0
        self.state = 'starting'
        self._last_status = 0.0
        self.chime_log_enabled = False      # `d` + `all` sent on this connect
        self.handle_chimes_lines = 0

    # --- records ----------------------------------------------------------
    def _write(self, rec: dict, text: str, loud: bool) -> None:
        ts = rec['t']
        rec['mono'] = round(uptime(), 3)        # seconds since boot: right even when the clock is not
        if self.boot is not None:
            rec['boot'] = self.boot
        stamp = time.strftime('%H:%M:%S', time.localtime(ts)) + '.%03d' % int((ts % 1) * 1000)
        mark = '*** ' if loud else '    '
        line = '%s +%.3f %s%s' % (stamp, rec['mono'], mark, text)
        self.txt.write(line + '\n')
        self.jsonl.write(json.dumps(rec, separators=(',', ':')) + '\n')
        self.lines += 1
        if rec.get('chime_id') is not None and rec.get('chime'):
            self.last_chime = {'id': '0x%02x' % rec['chime_id'], 't': ts, 'mono': rec['mono']}
        if loud:
            self.echo(line)
        self.sync()

    def sync(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_sync >= self.sync_s:
            for f in (self.txt, self.jsonl):
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            self._last_sync = now
        if self.status_path and (force or now - self._last_status >= self.status_s):
            self.write_status()

    def write_status(self) -> None:
        st = {'state': self.state, 'pid': os.getpid(), 'boot': self.boot, 'mono': round(uptime(), 1),
              'clock': time.strftime('%Y-%m-%d %H:%M:%S'), 'file': self.txt.name,
              'records': self.lines, 'log_lines': self.log_lines,
              'last_line_mono': self.last_line_t, 'last_chime': self.last_chime,
              'chime_log_enabled': self.chime_log_enabled,
              'chime_ids': {'0x%02x' % k: v for k, v in sorted(self.chime_ids.items())}}
        tmp = self.status_path + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(st, f, indent=1)
                f.flush(); os.fsync(f.fileno())
            os.replace(tmp, self.status_path)
        except OSError:
            pass
        self._last_status = time.monotonic()

    def note(self, text: str) -> None:
        """A line from the logger itself (connect progress, link lost...)."""
        self._write({'t': self.clock(), 'kind': 'note', 'text': text}, '# ' + text, True)

    def console_text(self, text: str) -> None:
        """0x0401 text arrives in chunks that ignore line ends (as engine._console_text)."""
        buf = self._buf + text.replace('\r', '\n')
        *lines, self._buf = buf.split('\n')
        if len(self._buf) > 4096:
            lines.append(self._buf)
            self._buf = ''
        for ln in lines:
            if ln.strip():
                self.log_line(ln)

    def log_line(self, ln: str) -> None:
        rec = {'t': self.clock(), 'kind': 'log', 'text': ln}
        self.log_lines += 1
        self.last_line_t = round(uptime(), 3)
        if 'handle_chimes' in ln:
            self.handle_chimes_lines += 1
        m = CHIME_ID_RX.search(ln)
        if m:
            cid = int(m.group(1), 16)
            rec['chime_id'] = cid
            self.chime_ids[cid] = self.chime_ids.get(cid, 0) + 1
        loud = bool(CHIME_RX.search(ln)) and not QUIET_RX.search(ln)
        rec['chime'] = loud
        self._write(rec, ln, loud)

    def bus_event(self, cmd: int, data: bytes) -> None:
        ev = busevents.decode(cmd, data)
        rec = {'t': self.clock(), 'kind': 'bus', 'cmd': '0x%04x' % cmd, 'raw': bytes(data).hex()}
        loud = False
        if ev is None:
            text = 'bus 0x%04x %s' % (cmd, bytes(data).hex())
        elif ev.bus == 'can':
            cid, d = ev.fields['id'], bytes(ev.fields['data'])
            rec.update(bus='can', id='0x%03x' % cid, data=d.hex())
            text = 'CAN 0x%03x %s' % (cid, d.hex(' '))
            if cid in CHIME_CAN_IDS and len(d) >= 2:
                rec['chime_id'] = d[1]
                text += '   chime ID 0x%02x  b0=%02x b7=%s' % (d[1], d[0], '%02x' % d[7] if len(d) > 7 else '--')
                loud = d[1] > 1
                if loud:
                    self.chime_ids[d[1]] = self.chime_ids.get(d[1], 0) + 1
        else:
            rec.update(bus='a2b', sub=ev.sub, fields={k: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
                                                      for k, v in ev.fields.items()})
            text = 'A2B sub %s %s' % (ev.sub, bytes(data).hex(' '))
        rec['chime'] = loud
        self._write(rec, text, loud)

    def frame(self, fr) -> None:
        if getattr(fr, 'kind', None) == 'text' and getattr(fr, 'data', None):
            self.console_text(bytes(fr.data).decode('latin-1'))   # raw text outside MDI frames
            return
        m = getattr(fr, 'message', None)
        if fr.kind != 'mdi' or m is None:
            return
        if m.cmd == LOG_CMD:
            self.console_text(bytes(m.data).decode('latin-1').replace('\0', ''))
        elif m.cmd in busevents.ROUTED:
            self.bus_event(m.cmd, m.data)

    def summary(self) -> str:
        if not self.chime_ids:
            return 'no chime IDs seen'
        return 'chime IDs seen: ' + ', '.join('0x%02x x%d' % kv for kv in sorted(self.chime_ids.items()))

    def close(self) -> None:
        if self._buf.strip():
            self.log_line(self._buf)
            self._buf = ''
        self.sync(force=True)
        self.txt.close()
        self.jsonl.close()


def enable_frames(line: str) -> list[bytes]:
    """The official app's 0x0401 frames for one console line, each through the gate."""
    from . import parity, wire_apply
    frames = wire_apply.debug_send_frames(line)
    assert [f.hex() for f in frames] == list(ENABLE_FRAMES[line]), 'encoder drifted from the proven bytes'
    for f in frames:
        parity.assert_frame_cleared(f, LOG_CMD, 'chimelog --enable-chime-log %r' % line)
    return frames


def send_console_line(transport, log: ChimeLog, line: str) -> None:
    frames = enable_frames(line)
    for f in frames:
        transport.write(f)
    log._write({'t': log.clock(), 'kind': 'sent', 'line': line, 'frames': [f.hex() for f in frames]},
               '# sent console %r: %s' % (line, ' '.join(f.hex() for f in frames)), True)


def record(transport, log: ChimeLog, *, duration: float | None = None, keepalive: bool = True,
           poll_s: float = 0.05, connect_kw: dict | None = None, stop=None,
           enable_chime_log: bool = False, enable_gap_s: float = ENABLE_GAP_S):
    """Connect read-only, then record passively until `duration` (None = forever).
    With enable_chime_log, type `d` then `all` once after this connect.
    Raises whatever the transport raises when the link drops."""
    res = connect.run_connect(transport, log=log.note, **(connect_kw or {}))
    for chunk in res.log_lines:                          # log text read during connect
        log.console_text(chunk)
    log.note('connected: product 0x%04x app %s; recording' % (res.product_id or 0, res.app_version))
    log.state = 'recording'
    framer = MdiFramer()
    ping = framer.encode(MdiMessage(cmd=0x0001, data=b''))
    from . import parity
    parity.assert_frame_cleared(ping, 0x0001, 'chimelog keep-alive')
    end = None if duration is None else time.monotonic() + duration
    last_tx = time.monotonic()
    pending = list(ENABLE_LINES) if enable_chime_log else []
    next_line = time.monotonic()
    while end is None or time.monotonic() < end:
        if pending and time.monotonic() >= next_line:
            send_console_line(transport, log, pending.pop(0))
            next_line = time.monotonic() + enable_gap_s
            if not pending:
                log.chime_log_enabled = True
        if keepalive and time.monotonic() - last_tx >= KEEPALIVE_S:
            transport.write(ping)
            last_tx = time.monotonic()
        for fr in framer.feed(transport.read(4096, poll_s)):
            log.frame(fr)
        log.sync()
        if stop is not None and stop():
            break
    return res


def selftest(transport, log: ChimeLog, seconds: float = 15.0, out=print,
             enable_chime_log: bool = False) -> bool:
    """Port opens (caller), read-only handshake answers, device log lines arrive.
    With enable_chime_log, also sends `d` + `all` and reports whether handle_chimes
    lines arrive afterwards (records the full time window for that)."""
    stop = None if enable_chime_log else (lambda: log.log_lines >= 3)
    try:
        res = record(transport, log, duration=seconds, stop=stop, enable_chime_log=enable_chime_log)
    except Exception as e:
        out('FAIL: port/handshake error: %s' % e)
        return False
    if res.product_id is None:
        out('FAIL: the device did not answer the handshake (no product id)')
        return False
    if not log.log_lines:
        out('FAIL: connected (product 0x%04x, app %s) but no log lines in %.0f s'
            % (res.product_id, res.app_version, seconds))
        return False
    out('PASS: product 0x%04x app %s, %d log line(s) arriving; %s'
        % (res.product_id, res.app_version, log.log_lines, log.summary()))
    if enable_chime_log:
        if log.handle_chimes_lines:
            out('chime log: ON - %d handle_chimes line(s) arrived after d + all' % log.handle_chimes_lines)
        else:
            out('chime log: sent d + all, but no handle_chimes line in %.0f s (try a chime: door, key)'
                % seconds)
    elif not log.chime_ids:
        out('note: no handle_chimes line yet (expected with the CAN_FORD_APP log stream off)')
    return True


def main(argv=None) -> int:
    import argparse
    from .transport import open_transport
    ap = argparse.ArgumentParser(prog='python -m mobridge.chimelog',
                                 description='Passively record the Mobridge debug log and chime events (read-only).')
    ap.add_argument('port', nargs='?', help='rfc2217://host:4000, socket://host:port, /dev/ttyUSB0 or COMx')
    ap.add_argument('--port', dest='port_opt', help='same as the positional port')
    ap.add_argument('-o', '--out', default=None, help='output base path (.txt and .jsonl are added)')
    ap.add_argument('--dir', default=os.path.join(os.path.expanduser('~'), 'captures'),
                    help='folder for chimelog-boot<N>-<date-time>.{txt,jsonl} when --out is not given')
    ap.add_argument('--duration', type=float, default=None, help='seconds to record (default: until Ctrl+C)')
    ap.add_argument('--no-keepalive', action='store_true', help="don't send the app's 1 Hz 0x0001 keep-alive")
    ap.add_argument('--retry', type=float, default=3.0, help='seconds between reconnects after a dropped link')
    ap.add_argument('--selftest', action='store_true',
                    help='check port, handshake and incoming log lines, print PASS/FAIL, exit 0/1')
    ap.add_argument('--enable-chime-log', action='store_true',
                    help='after each connect, type the console lines d then all (turns on the '
                         'CAN_FORD_APP stream so handle_chimes prints; RAM-only, cleared at reboot)')
    ap.add_argument('--sync', type=float, default=1.0, help='fsync to disk at least this often (seconds)')
    a = ap.parse_args(argv)
    a.port = a.port_opt or a.port
    if not a.port:
        ap.error('give a port')
    os.makedirs(a.dir, exist_ok=True)
    boot = boot_number(os.path.join(a.dir, '.bootcount'))
    base = a.out or os.path.join(a.dir, 'chimelog-boot%03d-%s' % (boot, time.strftime('%Y%m%d-%H%M%S')))
    log = ChimeLog(base, sync_s=a.sync)
    log.boot = boot
    log.status_path = os.path.join(a.dir, 'chimelog-status.json')
    if a.selftest:
        t = None
        try:
            t = open_transport(a.port, connect.POWERON_BAUD)
            ok = selftest(t, log, seconds=30.0 if a.enable_chime_log else 15.0,
                          enable_chime_log=a.enable_chime_log)
        except Exception as e:
            print('FAIL: cannot open %s: %s' % (a.port, e))
            ok = False
        finally:
            if t is not None:
                t.close()
        log.state = 'selftest ' + ('PASS' if ok else 'FAIL')
        log.close()
        return 0 if ok else 1
    log.note('boot %d, %.1f s since boot, clock says %s (may be wrong without network)'
             % (boot, uptime(), time.strftime('%Y-%m-%d %H:%M:%S')))
    print('recording to %s.txt / .jsonl  (Ctrl+C to stop)' % base)
    end = None if a.duration is None else time.monotonic() + a.duration
    try:
        while end is None or time.monotonic() < end:
            t = None
            lock = None
            try:
                lock = uucp_lock(a.port)
                t = open_transport(a.port, connect.POWERON_BAUD)
                left = None if end is None else max(0.0, end - time.monotonic())
                record(t, log, duration=left, keepalive=not a.no_keepalive,
                       enable_chime_log=a.enable_chime_log)
                break
            except KeyboardInterrupt:
                raise
            except Exception as e:                       # unit off, cable pulled, bridge gone
                log.state = 'waiting for device'
                log.note('link lost: %s; retrying in %.0f s' % (e, a.retry))
                time.sleep(a.retry)
            finally:
                if t is not None:
                    try:
                        t.close()
                    except Exception:
                        pass
                if lock:
                    try:
                        os.remove(lock)
                    except OSError:
                        pass
    except KeyboardInterrupt:
        pass
    log.note(log.summary())
    log.state = 'stopped'
    log.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
