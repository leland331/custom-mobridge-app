import os
import shutil
import tempfile
import threading

import pytest

from fakes import TEST_WIREMAP, FakeFramer, FakeMdiDevice
from mobridge import zmodem
from mobridge.client import MobridgeClient
from mobridge.framing import FramingUnknown
from mobridge.safety import Policy
from mobridge.transport import Transport
from test_offline import make_fif

needs_rz = pytest.mark.skipif(not shutil.which('rz'), reason='lrzsz rz not installed')


def test_export_flow_end_to_end(tmp_path):
    flash = os.urandom(0x2000)
    dev = FakeMdiDevice(flash)
    seen = []
    c = MobridgeClient(dev, FakeFramer(), wiremap=TEST_WIREMAP, on_frame=seen.append, require_parity=False)
    assert c.sw_version() == '02.11.08'
    out = tmp_path / 'dump.bin'
    data = c.export_flash(0x100, 0x1000, str(out), chunk=200)
    assert data == flash[0x100:0x1100] == out.read_bytes()
    assert any(b'WSNDMGR' in f.data for f in seen)   # log noise routed aside, not mistaken for replies


def test_unconfirmed_framing_sends_nothing():
    dev = FakeMdiDevice(b'\0' * 16)
    with pytest.raises(FramingUnknown):
        MobridgeClient(dev).ping()          # default framer + empty wiremap
    with pytest.raises(FramingUnknown):
        MobridgeClient(dev, FakeFramer()).ping()   # real framer but no wire ids
    assert dev.sent == []


@needs_rz
def test_zmodem_against_lrzsz(tmp_path):
    from pipe_transport import PipeTransport
    data = os.urandom(200_000) + bytes(range(256)) * 64
    t = PipeTransport(['rz', '-b', '-y', '--escape'], cwd=str(tmp_path))
    zmodem.send_file(t, data, 'x.fif')
    t.close()
    assert (tmp_path / 'x.fif').read_bytes() == data


def test_every_catalogued_job_is_guarded():
    """With the default framer, no job of any safety class ever reaches the wire."""
    from mobridge import commands
    from mobridge.commands import Access
    from mobridge.safety import SafetyError
    dev = FakeMdiDevice(b'')
    c = MobridgeClient(dev, policy=Policy.allow(*Access), require_parity=False)
    for name in commands.COMMANDS:
        with pytest.raises((FramingUnknown, SafetyError)):
            c.job(name)
    ro = MobridgeClient(dev)
    for name, cmd in commands.COMMANDS.items():
        if cmd.access not in (Access.READ, Access.SESSION):
            with pytest.raises(SafetyError):
                ro.job(name)
    assert dev.sent == []
