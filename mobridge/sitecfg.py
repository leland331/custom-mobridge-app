"""Optional install-wide defaults (site.ini).

A plain install has no site.ini and uses the built-in defaults. An install can
place a site.ini next to the app (the folder holding the `mobridge` package or,
in the packaged build, the .exe) or point MOBRIDGE_SITE_INI at one:

    [Options]          ; defaults for cfg.ini keys (the user's cfg.ini still wins)
    networkport = rfc2217://bridge.example:4000

    [Paths]
    captures = ~/Documents/Custom Mobridge/captures
"""
from __future__ import annotations

import configparser
import os
import sys

DEFAULT_CAPTURES = os.path.join('~', 'Documents', 'Custom Mobridge', 'captures')


def _candidates() -> list[str]:
    env = os.environ.get('MOBRIDGE_SITE_INI')
    if env:
        return [env]
    out = []
    if getattr(sys, 'frozen', False):
        out.append(os.path.join(os.path.dirname(sys.executable), 'site.ini'))
    out.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'site.ini'))
    return out


def load(path: str | None = None) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    for p in ([path] if path else _candidates()):
        try:
            if p and os.path.isfile(p):
                cp.read(p)
                break
        except (OSError, configparser.Error):
            pass
    return cp


def option_defaults(path: str | None = None) -> dict:
    cp = load(path)
    return dict(cp['Options']) if cp.has_section('Options') else {}


def captures_dir(path: str | None = None) -> str:
    cp = load(path)
    raw = cp.get('Paths', 'captures', fallback=DEFAULT_CAPTURES)
    return os.path.expanduser(raw)
