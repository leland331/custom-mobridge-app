"""Offscreen GUI smoke tests. Skipped if PySide6/PyQt6 (or a platform) is missing."""
import os
import tempfile

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6', reason='Qt binding not installed')

from test_offline import make_fif   # noqa: E402


def _win(flags=True):
    from mobridge.gui.config import Options
    from mobridge.gui.ui import MainWindow
    cfg = os.path.join(tempfile.mkdtemp(), 'cfg.ini')
    o = Options(cfg)
    o.set('mobridgedebug', flags); o.set('scripting', flags); o.save()
    return MainWindow(options=Options(cfg))


def test_all_screens_build_and_switch(app):
    from mobridge.gui import ui
    win = _win()
    assert win.stack.count() == len(ui.OFFICIAL_TABS) + len(ui.EXTRA_TABS)
    for i in range(win.stack.count()):
        win.stack.setCurrentIndex(i)
        assert win.stack.currentWidget().sizeHint().isValid()


def test_flag_gated_screens_hidden_without_flags(app):
    titles = [s.title for s in _win(flags=False).screens]
    assert 'Debug Console' not in titles and 'Script' not in titles
    assert 'Debug Console' in [s.title for s in _win(flags=True).screens]


def test_firmware_screen_validates_offline(app, tmp_path):
    win = _win()
    fw = next(s for s in win.screens if s.title == 'Firmware')
    p = tmp_path / 'img.fif'; p.write_bytes(make_fif())
    fw.path.setText(str(p)); fw._check()
    assert 'Image is valid and matches this device.' in fw.report.toPlainText()
    assert fw.flash_btn.isEnabled()
    bad = tmp_path / 'bad.fif'; bad.write_bytes(make_fif(flash_off=0))
    fw.path.setText(str(bad)); fw._check()
    assert 'bootblock' in fw.report.toPlainText() and not fw.flash_btn.isEnabled()


def test_debug_console_safety_classifies(app):
    win = _win()
    dc = next(s for s in win.screens if s.title == 'Debug Console')
    dc.input.setText('spiflash read 0x60000')
    assert dc.cls.currentText() == 'read'
    dc.input.setText('spiflash erase 0x0 0x1000')
    assert 'Not allowed' in dc.cls.currentText()


def test_firmware_library_scans_nested_folders(app, tmp_path):
    from mobridge.gui.screens.firmware import find_fifs
    for rel in ('top.fif', 'official/a.fif', 'our firmwares/bug-fix/b.fif',
                'our firmwares/features/c.fif', 'a/b/c/d/e/too_deep.fif', 'notes.txt'):
        p = tmp_path / rel; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_fif())
    names = [os.path.basename(f) for f in find_fifs(str(tmp_path))]
    assert sorted(names) == ['a.fif', 'b.fif', 'c.fif', 'top.fif']
    assert len(find_fifs(str(tmp_path), limit=2)) == 2
    win = _win()
    fw = next(s for s in win.screens if s.title == 'Firmware')
    fw._folders = [str(tmp_path)]; fw._rescan()
    assert fw.library.count() == 4
