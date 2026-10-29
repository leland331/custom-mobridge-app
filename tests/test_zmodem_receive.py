"""ZMODEM receive (temp.ini after the 0x0282 ack), checked against lrzsz `sz`."""
import os
import shutil

import pytest

from mobridge import zmodem
from pipe_transport import PipeTransport


@pytest.mark.skipif(not shutil.which('sz'), reason='lrzsz sz not installed')
@pytest.mark.parametrize('size', [0, 300, 5000, 70000])
def test_receive_file_from_lrzsz_sz(tmp_path, size):
    body = os.urandom(size) if size else b''
    src = tmp_path / 'temp.ini'
    src.write_bytes(body)
    t = PipeTransport(['sz', '-q', str(src)])
    try:
        name, data = zmodem.receive_file(t, timeout=5.0, send_init=False)
    finally:
        t.close()
    assert name == 'temp.ini' and data == body
