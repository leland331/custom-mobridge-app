"""cfg.ini [Options] flags, same keys as the official app, so the same
visibility rules apply (Debug Console needs mobridgedebug, Script needs scripting)."""
from __future__ import annotations

import configparser
import os

from .. import sitecfg as _site

KEYS = ('mobridgedebug', 'bavsounddebug', 'dabdebug', 'dabeol', 'scripting', 'products', 'remoteurl', 'networkport')
DEFAULTS = {k: 'false' for k in KEYS}
DEFAULTS['remoteurl'] = ''
DEFAULTS['networkport'] = ''   # e.g. rfc2217://host:4000 (ser2net); empty = USB only


def _path() -> str:
    base = os.environ.get('APPDATA') or os.path.expanduser('~/.config')
    return os.path.join(base, 'mObridge', 'mObridge DSP', 'cfg.ini')


class Options:
    def __init__(self, path: str | None = None):
        self.path = path or _path()
        self._cp = configparser.ConfigParser()
        self._cp['Options'] = {**DEFAULTS, **{k: v for k, v in _site.option_defaults().items() if k in KEYS}}
        try:
            self._cp.read(self.path)
        except (OSError, configparser.Error):
            pass
        if not self._cp.has_section('Options'):
            self._cp['Options'] = dict(DEFAULTS)

    def bool(self, key: str) -> bool:
        return str(self._cp['Options'].get(key, 'false')).strip().lower() in ('1', 'true', 'yes', 'on')

    def get(self, key: str) -> str:
        return self._cp['Options'].get(key, DEFAULTS.get(key, ''))

    def set(self, key: str, value) -> None:
        self._cp['Options'][key] = 'true' if value is True else 'false' if value is False else str(value)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            self._cp.write(f)
