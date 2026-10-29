"""Post-flash version check (BOOTLOADER_DISASM_OCT23.md §5b): sub 8 ends a good flash,
a product mismatch and a verify failure alike, so the engine compares the version the
unit reports after Reset + reconnect with the .fif's. Normal update and recovery."""
import pytest

from mobridge import connect, flashflow as F, uirules as R
from mobridge.session import Job
from test_offline import make_fif
from test_recovery_mdi import _QuietPort

FIF_VER = 0x00020B0E          # 2.11.14
OLD = '2.11.4'


def _engine(app, tmp_path, monkeypatch, before):
    from mobridge.gui.engine import Engine
    e = Engine()
    e.port = 'COM9'
    e.conn_state = before
    p = tmp_path / 'fw.fif'
    p.write_bytes(make_fif(version=FIF_VER))
    plan = F.validate(str(p), 0x202C, (2, 11, 4), before)
    resets, errors, phases = [], [], []
    monkeypatch.setattr(e, '_submit_device_job', lambda name, cmd, tries: resets.append(cmd))
    monkeypatch.setattr(e, 'connect_device', lambda *a, **k: None)
    e.error.connect(errors.append)
    e.flash_status.connect(lambda kind, a, b: kind == 'phase' and phases.append(a))

    class _S:                                    # runs the FlashFile job inline
        def submit(self, job: Job):
            job.run(self, job)
            job.done(job)
    e.session = _S()
    monkeypatch.setattr(F, 'run_flash', lambda s, data, name, status: (status('phase', 8),
                                                                      status('phase', 10), True)[2])
    assert e._start_flash(plan)
    app.processEvents()                          # the queued _flash_done
    e.session = None
    return e, resets, errors, phases


def _reconnect(e, product, version, model=None):
    from mobridge.modes import DeviceMode
    res = connect.ConnectResult(product_id=product, app_version=version, mode=DeviceMode.MDI,
                                model=model)
    e._apply_connect_result(res, _QuietPort())
    e._drop_session()


TARGETS = [R.STATE_CONNECTED, R.STATE_RECOVERY]


@pytest.mark.parametrize('before', TARGETS)
def test_match_shows_complete(app, tmp_path, monkeypatch, before):
    e, resets, errors, phases = _engine(app, tmp_path, monkeypatch, before)
    assert resets == [0x0010] and phases == [8]          # no "Complete!" before the check
    _reconnect(e, 0x202C, '2.11.14')
    assert phases == [8, 10] and errors == [] and e._flash_expect is None


@pytest.mark.parametrize('before', TARGETS)
@pytest.mark.parametrize('product,version', [(0x2002, OLD),      # product mismatch: old image stays
                                             (0x202C, OLD)])     # verify failure: old version back
def test_mismatch_shows_error(app, tmp_path, monkeypatch, before, product, version):
    e, _, errors, phases = _engine(app, tmp_path, monkeypatch, before)
    if before == R.STATE_CONNECTED:
        e.write_back_model = e.root                      # a pending write-back must not run
        pushes = []
        monkeypatch.setattr(e, 'push', pushes.append)
    _reconnect(e, product, version)
    app.processEvents()
    assert phases == [8, 11] and errors == [R.FLASH_STATUS[11]]
    if before == R.STATE_CONNECTED:
        assert pushes == []


def _partial():
    """What a version-only read returns: a fresh model with the new firmware's fw."""
    from mobridge.model import DspModel
    m = DspModel()
    m.fw = 0x7777
    return m


def _armed(app, tmp_path, monkeypatch, before):
    e, resets, errors, phases = _engine(app, tmp_path, monkeypatch, before)
    pushes = []
    monkeypatch.setattr(e, 'push', pushes.append)
    tune = e.root
    if before == R.STATE_CONNECTED:
        e.write_back_model = tune                        # the app's tune, to write back
    return e, errors, phases, pushes, tune


@pytest.mark.parametrize('before', TARGETS)
def test_no_reconnect_shows_error(app, tmp_path, monkeypatch, before):
    e, errors, phases, pushes, tune = _armed(app, tmp_path, monkeypatch, before)
    e._post_flash_timeout(e._flash_token)
    assert phases == [8, 11] and errors == [R.FLASH_STATUS[11]] and e.conn_state == 14
    # the flags stay: a late return with the new version still gets its write-back
    assert e.post_flash == (before == R.STATE_CONNECTED) and e._flash_expect is not None
    _reconnect(e, 0x202C, '2.11.14', _partial())
    app.processEvents()
    assert errors == [R.FLASH_STATUS[11]] and phases == [8, 11, 10]
    assert not e.post_flash and e.write_back_model is None and e._flash_expect is None
    if before == R.STATE_CONNECTED:
        assert e.root is tune and tune.fw == 0x7777 and pushes == ['write-back']


def test_late_return_old_version_after_timeout_no_write_back(app, tmp_path, monkeypatch):
    e, errors, phases, pushes, tune = _armed(app, tmp_path, monkeypatch, R.STATE_CONNECTED)
    e._post_flash_timeout(e._flash_token)
    _reconnect(e, 0x202C, OLD, _partial())
    app.processEvents()
    assert errors == [R.FLASH_STATUS[11]] and phases == [8, 11]      # "Error!" once
    assert pushes == [] and e.root is tune and tune.fw != 0x7777


@pytest.mark.parametrize('before', TARGETS)
def test_reset_never_answered_shows_error(app, tmp_path, monkeypatch, before):
    e, errors, phases, pushes, tune = _armed(app, tmp_path, monkeypatch, before)
    e._device_job_done('Reset', 0x0010, e.RESET_RETRIES, None)
    assert phases == [8, 11] and errors == [R.FLASH_STATUS[11]] and e.conn_state == before
    assert e.post_flash == (before == R.STATE_CONNECTED) and e._flash_expect is not None
    e._post_flash_timeout(e._flash_token)                # "Error!" not shown twice
    assert errors == [R.FLASH_STATUS[11]]
    _reconnect(e, 0x202C, '2.11.14', _partial())         # the unit returns later, updated
    app.processEvents()
    assert phases == [8, 11, 10] and errors == [R.FLASH_STATUS[11]]
    if before == R.STATE_CONNECTED:
        assert e.root is tune and pushes == ['write-back']


@pytest.mark.parametrize('before', TARGETS)
def test_booting_unit_silent_identify_is_not_a_mismatch(app, tmp_path, monkeypatch, before):
    e, errors, phases, pushes, tune = _armed(app, tmp_path, monkeypatch, before)
    for product, version in ((None, None), (0x202C, None)):   # no reply / no version yet
        _reconnect(e, product, version, _partial())
        app.processEvents()
        assert phases == [8] and errors == [] and pushes == []
        assert e.conn_state == 7 and e._reconnecting and not e.connected
        assert e.post_flash == (before == R.STATE_CONNECTED) and e._flash_expect is not None
        assert e.root is tune
    _reconnect(e, 0x202C, '2.11.14', _partial())
    app.processEvents()
    assert phases == [8, 10] and errors == []
    if before == R.STATE_CONNECTED:
        assert e.root is tune and pushes == ['write-back']


def test_version_only_read_racing_the_timeout_keeps_the_tune(app, tmp_path, monkeypatch):
    e, errors, phases, pushes, tune = _armed(app, tmp_path, monkeypatch, R.STATE_CONNECTED)
    e._conn_partial = True                               # _start_connect: a version-only read runs
    e._post_flash_timeout(e._flash_token)                # ...and the deadline passes meanwhile
    e.post_flash = False                                 # even if the flag were gone
    _reconnect(e, 0x202C, '2.11.14', _partial())
    app.processEvents()
    assert e.root is tune and tune.fw == 0x7777          # the partial read never replaces the tune
    assert pushes == ['write-back'] and not e._conn_partial


@pytest.mark.parametrize('state', [R.STATE_UPDATING, 7, 10])
def test_start_recovery_refused_mid_flash(app, tmp_path, monkeypatch, state):
    e, errors, phases, pushes, tune = _armed(app, tmp_path, monkeypatch, R.STATE_CONNECTED)
    drops, connects = [], []
    monkeypatch.setattr(e, '_drop_session', lambda: drops.append(1))
    monkeypatch.setattr(e, 'connect_device', lambda *a, **k: connects.append(a))
    e.conn_state = state
    assert e.start_recovery() is False
    assert drops == [] and connects == [] and not e._recovery_next


def test_stale_timer_after_match_is_ignored(app, tmp_path, monkeypatch):
    e, _, errors, phases = _engine(app, tmp_path, monkeypatch, R.STATE_CONNECTED)
    _reconnect(e, 0x202C, '2.11.14')
    e._post_flash_timeout(e._flash_token)
    assert phases == [8, 10] and errors == []
