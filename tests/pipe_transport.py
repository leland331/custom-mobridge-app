"""Test helper: a Transport talking to a subprocess's stdin/stdout (e.g. lrzsz rz)."""
import os
import select
import subprocess

from mobridge.transport import Transport


class PipeTransport(Transport):
    def __init__(self, argv, cwd=None):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, cwd=cwd, bufsize=0)

    def write(self, data):
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def read(self, size=4096, timeout=0.1):
        r, _, _ = select.select([self.p.stdout], [], [], timeout)
        return os.read(self.p.stdout.fileno(), size) if r else b''

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        self.p.wait(10)
