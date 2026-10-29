"""Save model mirrors the official app: live edits PERSIST on the device; "Save"
is a .rax file backup to the PC, and the dirty state tracks "not yet backed up"."""
import json
import pytest


def test_live_has_no_device_commit():
    # There is no device-side commit/save command to gate -- live edits persist.
    from mobridge import dspwrite
    assert not any(n.lower().startswith('commit') for n in dir(dspwrite))


def test_export_backup_writes_file_and_clears_dirty(app, tmp_path):
    from mobridge.gui.engine import Engine
    e = Engine()
    seen = []
    e.dirty_changed.connect(seen.append)
    e._pending['channel_gain'] = -3.0
    assert e.dirty

    out = tmp_path / 'backup.rax'
    written = e.export_backup(str(out))
    assert out.exists()
    doc = json.loads(out.read_text())
    assert doc['settings'] == {'channel_gain': -3.0}
    # backing up to a file clears the 'needs backup' state (device keeps the edit)
    assert not e.dirty
    assert seen[-1] is False


def test_discard_clears_without_file(app):
    from mobridge.gui.engine import Engine
    e = Engine()
    e._pending['eq_gain'] = 2.0
    assert e.dirty
    e.discard()
    assert not e.dirty


def test_close_choices(app, tmp_path):
    from mobridge.gui.ui import MainWindow
    win = MainWindow()
    win.engine._pending['eq_gain'] = 2.0
    # Cancel keeps the window open.
    assert win._apply_close_choice('cancel') is False
    assert win.engine.dirty
    # Save with no path chosen stays open.
    assert win._apply_close_choice('save', None) is False
    assert win.engine.dirty
    # Save with a path writes the backup and allows close.
    out = tmp_path / 'b.rax'
    assert win._apply_close_choice('save', str(out)) is True
    assert out.exists() and not win.engine.dirty
    # Discard allows close too.
    win.engine._pending['x'] = 1.0
    assert win._apply_close_choice('discard') is True
    assert not win.engine.dirty
