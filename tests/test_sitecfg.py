"""site.ini: optional install-wide defaults; a plain install uses built-ins."""
import os

from mobridge import sitecfg
from mobridge.gui.config import Options


def test_no_site_ini_means_builtin_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv('MOBRIDGE_SITE_INI', str(tmp_path / 'missing.ini'))
    assert sitecfg.option_defaults() == {}
    assert sitecfg.captures_dir() == os.path.expanduser(sitecfg.DEFAULT_CAPTURES)
    assert Options(str(tmp_path / 'cfg.ini')).get('networkport') == ''


def test_site_ini_sets_defaults_and_user_cfg_wins(tmp_path, monkeypatch):
    site = tmp_path / 'site.ini'
    site.write_text('[Options]\nnetworkport = rfc2217://bridge.example:4000\nbogus = 1\n'
                    '[Paths]\ncaptures = ~/caps\n')
    monkeypatch.setenv('MOBRIDGE_SITE_INI', str(site))
    assert sitecfg.captures_dir() == os.path.expanduser('~/caps')
    o = Options(str(tmp_path / 'cfg.ini'))
    assert o.get('networkport') == 'rfc2217://bridge.example:4000'
    assert 'bogus' not in dict(o._cp['Options'])
    user = tmp_path / 'user.ini'
    user.write_text('[Options]\nnetworkport = \n')
    assert Options(str(user)).get('networkport') == ''
