"""mobridge: command-line front end.  `python -m mobridge --help`"""
from __future__ import annotations

import argparse
import sys

from . import boots, commands, fif
from .commands import Access
from .framing import FramingUnknown, get_framer
from .safety import Policy, SafetyError, classify_console
from .transport import DEFAULT_BAUD, list_ports, open_transport


def _policy(args) -> Policy:
    return Policy.allow(*(Access(a) for a in (args.allow or [])))


def cmd_ports(args):
    ports = list_ports()
    if not ports:
        print('no serial ports found')
    for dev, desc, ftdi in ports:
        print('%-12s %s%s' % (dev, desc, '   <- FTDI (likely the Mobridge)' if ftdi else ''))


def cmd_fif(args):
    rc = 0
    for p in args.files:
        try:
            info, _ = fif.load(p)
        except (OSError, fif.FifError) as e:
            print('%s: %s' % (p, e)); rc = 1; continue
        print(fif.describe(info))
        problems = fif.flash_problems(info, None if args.any_product else fif.PRODUCT_DA_G2_PRO_A2B_FORD)
        print('flashable     : %s' % ('yes' if not problems else 'NO - ' + '; '.join(problems)))
        print()
        rc |= bool(problems)
    return rc


def cmd_commands(args):
    for c in commands.COMMANDS.values():
        print('%-14s %-8s %-12s %s%s' % (c.name, c.group, c.access.value, c.doc,
                                        ('  [fw: %s]' % c.fw_handler) if c.fw_handler else ''))


def cmd_listen(args):
    from .client import MobridgeClient
    with open_transport(args.port, args.baud, args.capture) as t:
        r = MobridgeClient(t).listen(args.seconds, echo=None if args.quiet else print)
    print('\n-- %d bytes, %d lines. %s' % (r.bytes_seen, len(r.text_lines), r.note))
    for k, v in r.info.items():
        print('   %s: %s' % (k, v))


def cmd_console(args):
    """Line console with the safety filter. The device's log flood will interleave with output."""
    import threading
    from .client import MobridgeClient
    with open_transport(args.port, args.baud, args.capture) as t:
        c = MobridgeClient(t, policy=_policy(args))
        stop = threading.Event()

        def pump():
            while not stop.is_set():
                d = t.read(4096, 0.1)
                if d:
                    sys.stdout.write(d.decode('latin-1')); sys.stdout.flush()
        th = threading.Thread(target=pump, daemon=True); th.start()
        try:
            for line in sys.stdin:
                try:
                    c.console_send(line.rstrip('\n'))
                except SafetyError as e:
                    print('[blocked] %s' % e)
        except KeyboardInterrupt:
            pass
        stop.set()


def cmd_classify(args):
    line = ' '.join(args.line)
    try:
        print(classify_console(line).value)
    except SafetyError as e:
        print('blocked: %s' % e); return 1


def cmd_export(args):
    from .client import FLASH_REGIONS, MobridgeClient
    addr, size = FLASH_REGIONS[args.region] if args.region else (int(args.addr, 0), int(args.size, 0))
    with open_transport(args.port, args.baud, args.capture) as t:
        c = MobridgeClient(t, get_framer(args.framer), _policy(args))
        c.export_flash(addr, size, args.out,
                       progress=lambda d, n: print('\r%d / %d' % (d, n), end='', flush=True))
    print('\nwrote %s' % args.out)


def cmd_flash(args):
    if args.execute and not args.port:
        print('--execute needs --port'); return 2
    # The product check runs against the unit's own recovery identify (as the exe does).
    try:
        if not args.execute:
            boots.BootsFlasher(None, _policy(args)).flash(args.file, execute=False)
            return
        with open_transport(args.port, args.baud, args.capture) as t:
            st = boots.BootsFlasher(t, _policy(args)).flash(args.file, execute=True)
    except ValueError as e:
        print(e); return 2
    print('result: %s' % st.state)
    return 0 if st.state == 'complete' else 1


def cmd_recovery_id(args):
    with open_transport(args.port, args.baud, args.capture) as t:
        b = boots.BootsFlasher(t, _policy(args))
        ident = b.identify()
        print('product %#06x  version %d.%d.%d  (%s)' % ((ident.product,) + ident.version + (ident.language,)))
        b.exit_recovery(ident)


def cmd_rosetta(args):
    """Load the reference real device dumps and report Rosetta-stone readiness (offline, read-only)."""
    from . import encoders, stored_config
    dumps = stored_config.load_dumps(args.dir)
    if dumps['nvm'] is None and not dumps['rax']:
        print('no NVM settings .bin or .rax files found in %s' % args.dir)
        print('(unzip device-dumps.zip there; .rax backups still pending from the laptop)')
        return 1
    if dumps['nvm'] is not None:
        sm = stored_config.nvm_summary(dumps['nvm'])
        print('NVM settings: %d bytes, magic=%s, %d written (%.1f%%), %d region(s):'
              % (sm['size'], sm['has_magic'], sm['written_bytes'], sm['written_pct'], len(sm['regions'])))
        for a, b in sm['regions']:
            print('   0x%05x-0x%05x  %d bytes' % (a, b, b - a))
    print('.rax config backups loaded: %d' % len(dumps['rax']))
    conf = encoders.confirmed_encoders()
    print('\nconfirmed value encoders: %s' % (', '.join(conf) or 'none yet'))
    print('Rosetta round-trip needs: (a) the NVM field map (DEVICE_CONFIG.md, other thread) and '
          '(b) confirmed scaling encoders (from the firmware dsp_nvm_file.c / app-side scaling). '
          'Until both land, value encodings stay BLOCKED — no guesses.')
    return 0


def cmd_parity(args):
    """Byte-compare our encoder against reference/captured frames (offline)."""
    from . import parity
    rows = parity.check_all(args.references) if args.references else parity.check_all()
    ok_all = True
    for ref, ok, got in rows:
        ok_all &= ok
        mark = 'OK ' if ok else 'MISMATCH'
        print('%-22s cmd=%04x  %-16s %s' % (ref.name, ref.cmd, ref.source.name, mark))
        if not ok:
            print('   expected %s' % ref.expected.hex(' '))
            print('   got      %s' % got.hex(' '))
    cleared = sorted({ref.cmd for ref, ok, _ in rows if ok and ref.source.clears_sending})
    # The real send gate is frame-level (parity.frame_cleared): a command opens for
    # ANY payload only if it is in SEND_SAFE_COMMANDS (reads); otherwise just the
    # exact captured frames clear. Report the two tiers separately so a write command
    # with a captured frame is never misread as "open".
    anypay = sorted(c for c in cleared if c in parity.SEND_SAFE_COMMANDS)
    exact_only = sorted(c for c in cleared if c not in parity.SEND_SAFE_COMMANDS)
    print('\n%d/%d reference frames reproduced.' % (sum(1 for _, ok, _ in rows if ok), len(rows)))
    print('  clear to send, ANY payload (reads/connect): %s'
          % (', '.join('%04x' % c for c in anypay) or 'none'))
    print('  clear to send, BYTE-EXACT captured frames only (writes stay otherwise gated): %s'
          % (', '.join('%04x' % c for c in exact_only) or 'none'))
    return 0 if ok_all else 1


def cmd_job(args):
    from .client import MobridgeClient
    data = bytes.fromhex(args.hex or '')
    commands.get(args.name)   # fail fast on a typo, before opening the port
    with open_transport(args.port, args.baud, args.capture) as t:
        c = MobridgeClient(t, get_framer(args.framer), _policy(args))
        r = c.job(args.name, data, args.timeout)
    print(r)
    print(r.data.hex(' '))


def cmd_replay(args):
    """Run a framer over a recorded capture: the test bench for a new framer."""
    import json
    framers = {'tx': get_framer(args.framer), 'rx': get_framer(args.framer)}
    counts = {}
    with open(args.jsonl, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            if 'hex' not in r or (args.dir and r['dir'] != args.dir):
                continue
            for fr in framers[r['dir']].feed(bytes.fromhex(r['hex'])):
                counts[fr.kind] = counts.get(fr.kind, 0) + 1
                if fr.kind == 'text' and not args.show_text:
                    continue
                desc = str(fr.message) if fr.message else (fr.text if fr.kind == 'text' else fr.data.hex(' '))
                print('%10.6f %s %-5s %s' % (r['t'], r['dir'], fr.kind, desc))
    print('-- frames: %s' % ', '.join('%s=%d' % kv for kv in sorted(counts.items())))


def main(argv=None):
    ap = argparse.ArgumentParser(prog='mobridge', description='Open client for Mobridge DA-G2 devices')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def dev(p):
        p.add_argument('--port', help='COM3, /dev/ttyUSB0, or ftdi://ftdi:232r/1')
        p.add_argument('--baud', type=int, default=DEFAULT_BAUD)
        p.add_argument('--capture', help='append every tx/rx byte to this .jsonl file')
        p.add_argument('--allow', action='append', choices=[a.value for a in Access],
                       help='permit a non-read access level (repeatable). Default: read-only')
        return p

    sub.add_parser('ports', help='list serial ports').set_defaults(f=cmd_ports)
    p = sub.add_parser('fif', help='validate .fif images (offline)')
    p.add_argument('files', nargs='+'); p.add_argument('--any-product', action='store_true')
    p.set_defaults(f=cmd_fif)
    sub.add_parser('commands', help='list known MDI jobs and their safety class').set_defaults(f=cmd_commands)
    p = sub.add_parser('classify', help='show how the safety filter treats a console line (offline)')
    p.add_argument('line', nargs='+'); p.set_defaults(f=cmd_classify)
    p = dev(sub.add_parser('listen', help='passively read what the device sends; sends nothing'))
    p.add_argument('--seconds', type=float, default=5.0); p.add_argument('--quiet', action='store_true')
    p.set_defaults(f=cmd_listen)
    dev(sub.add_parser('console', help='text console with the safety filter')).set_defaults(f=cmd_console)
    p = dev(sub.add_parser('export', help='read flash over MDI (needs confirmed framing)'))
    p.add_argument('--region', choices=['bootblock', 'app', 'all'])
    p.add_argument('--addr'); p.add_argument('--size'); p.add_argument('--out', required=True)
    p.add_argument('--framer', default='unconfirmed')
    p.set_defaults(f=cmd_export)
    p = dev(sub.add_parser('flash', help='BOOTS-mode .fif flash; dry run unless --execute --allow write'))
    p.add_argument('file'); p.add_argument('--execute', action='store_true')
    p.set_defaults(f=cmd_flash)
    p = dev(sub.add_parser('recovery-id', help='identify a unit sitting in the bootloader (read-only), then exit'))
    p.set_defaults(f=cmd_recovery_id)

    p = sub.add_parser('rosetta', help="report readiness of the reference device dumps for value validation (offline)")
    p.add_argument('--dir', required=True, help='directory with the unzipped device dumps')
    p.set_defaults(f=cmd_rosetta)
    p = sub.add_parser('parity', help='byte-compare our frames against reference/captures (offline)')
    p.add_argument('--references', help='path to a reference_frames.json (default: built-in)')
    p.set_defaults(f=cmd_parity)
    p = dev(sub.add_parser('job', help='run any MDI job by name (needs confirmed framing)'))
    p.add_argument('name'); p.add_argument('--hex', help='request payload as hex')
    p.add_argument('--framer', default='unconfirmed'); p.add_argument('--timeout', type=float, default=2.0)
    p.set_defaults(f=cmd_job)
    p = sub.add_parser('replay', help='decode a .jsonl capture with a framer (offline)')
    p.add_argument('jsonl'); p.add_argument('--framer', default='unconfirmed')
    p.add_argument('--dir', choices=['tx', 'rx']); p.add_argument('--show-text', action='store_true')
    p.set_defaults(f=cmd_replay)

    args = ap.parse_args(argv)
    if getattr(args, 'cmd', None) in ('listen', 'console', 'export', 'job') and not args.port:
        ap.error('--port is required')
    try:
        return args.f(args) or 0
    except (SafetyError, FramingUnknown, ValueError, TimeoutError, KeyError, __import__('mobridge.parity', fromlist=['ParityError']).ParityError) as e:
        print('error: %s' % e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
