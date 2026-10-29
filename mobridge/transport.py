"""Byte transports: the lowest layer, knows nothing about MDI.

The real app talks to the device through FTDI D2XX (Ftd2xx.dll). On Windows the
same FTDI chip also shows up as a VCP COM port (the reference unit: COM3), so plain
pyserial works and is the default. pyftdi is offered for Linux/macOS setups
that want to bypass the kernel driver.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod

FTDI_VID = 0x0403

# The app negotiates the UART speed up for transfers ("Baud=" strings);
# these are the rates found in the binary. The power-on rate is CONFIRMED by the
# 2026-10-22 capture (FTDI opens at divisor 0x001a = 115200, then SetBaud 230400).
KNOWN_BAUDS = (19200, 115200, 230400, 460800)
DEFAULT_BAUD = 115200  # CONFIRMED (2026-10-22 capture): app's initial FTDI rate


class Transport(ABC):
    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, size: int = 4096, timeout: float = 0.1) -> bytes:
        """Return up to `size` bytes, or b'' after `timeout` seconds with nothing."""

    def set_baud(self, baud: int) -> None:
        raise NotImplementedError

    def flush_input(self) -> None:
        while self.read(4096, 0):
            pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


NETWORK_SCHEMES = ('rfc2217://', 'socket://')

# A network port can vanish without a goodbye (the vehicle cuts the Pi's power).
# TCP keepalive plus a send timeout turn that silence into a socket error within
# a few seconds, which the session then reports exactly like a pulled USB cable.
NET_KEEPALIVE_IDLE_S = 2
NET_KEEPALIVE_INTERVAL_S = 1
NET_KEEPALIVE_COUNT = 3
NET_SEND_TIMEOUT_S = 5


def is_network_port(port: str) -> bool:
    return str(port).lower().startswith(NETWORK_SCHEMES)


def _harden_socket(sock) -> None:
    """Best effort: detect a dead peer quickly instead of hanging forever."""
    import socket
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return
    if hasattr(socket, 'SIO_KEEPALIVE_VALS'):                 # Windows
        try:
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, NET_KEEPALIVE_IDLE_S * 1000,
                                                    NET_KEEPALIVE_INTERVAL_S * 1000))
        except (OSError, ValueError):
            pass
        try:
            sock.setsockopt(socket.IPPROTO_TCP, 5, NET_SEND_TIMEOUT_S)   # TCP_MAXRT
        except OSError:
            pass
        return
    for name, val in (('TCP_KEEPIDLE', NET_KEEPALIVE_IDLE_S), ('TCP_KEEPALIVE', NET_KEEPALIVE_IDLE_S),
                      ('TCP_KEEPINTVL', NET_KEEPALIVE_INTERVAL_S), ('TCP_KEEPCNT', NET_KEEPALIVE_COUNT),
                      ('TCP_USER_TIMEOUT', NET_SEND_TIMEOUT_S * 1000)):
        opt = getattr(socket, name, None)
        if opt is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, opt, val)
            except OSError:
                pass


class SerialTransport(Transport):
    """pyserial: COM3 on Windows, /dev/ttyUSB0 on Linux, or a network port
    (rfc2217://host:port, e.g. a Raspberry Pi running ser2net; socket://host:port)."""

    def __init__(self, port: str, baud: int = DEFAULT_BAUD):
        import serial  # imported lazily so the offline tools work without it
        self._net = is_network_port(port)
        if self._net:
            # pyserial's rfc2217 has no write_timeout; the socket's send timeout
            # (_harden_socket) bounds a stuck write there instead.
            wt = None if port.lower().startswith('rfc2217://') else 5
            self._s = serial.serial_for_url(port, baudrate=baud, timeout=0, write_timeout=wt)
            sock = getattr(self._s, '_socket', None)
            if sock is not None:
                _harden_socket(sock)
        else:
            self._s = serial.Serial(port, baud, timeout=0, write_timeout=5)

    def write(self, data: bytes) -> None:
        self._s.write(data)

    def read(self, size=4096, timeout=0.1) -> bytes:
        if self._net:
            # Over rfc2217, setting .timeout reconfigures the remote port and waits for
            # the bridge's ack, so never touch it here: poll the receive buffer instead.
            deadline = time.monotonic() + timeout
            while True:
                n = self._s.in_waiting
                if n:
                    return self._s.read(min(size, n))
                if time.monotonic() >= deadline:
                    return b''
                time.sleep(0.002)
        self._s.timeout = timeout
        first = self._s.read(1)
        if not first:
            return b''
        self._s.timeout = 0
        return first + self._s.read(min(size - 1, max(self._s.in_waiting, 0)))

    def set_baud(self, baud: int) -> None:
        self._s.baudrate = baud

    def close(self) -> None:
        self._s.close()


class FtdiTransport(Transport):
    """pyftdi, e.g. url='ftdi://ftdi:232r/1'. Optional dependency."""

    def __init__(self, url: str, baud: int = DEFAULT_BAUD):
        from pyftdi.serialext import serial_for_url
        self._s = serial_for_url(url, baudrate=baud, timeout=0)

    write = SerialTransport.write
    read = SerialTransport.read
    set_baud = SerialTransport.set_baud
    close = SerialTransport.close


class RecordingTransport(Transport):
    """Wraps another transport and logs every byte both ways as JSON lines.

    This is the tool for pinning down the real framing: run the stock app's
    traffic through a tap, or record what the device sends us, then diff.
    """

    def __init__(self, inner: Transport, path: str):
        self.inner = inner
        self._f = open(path, 'a', encoding='utf-8')
        self._t0 = time.monotonic()

    def _log(self, direction: str, data: bytes) -> None:
        if data:
            self._f.write(json.dumps({'t': round(time.monotonic() - self._t0, 6),
                                      'dir': direction, 'hex': data.hex()}) + '\n')
            self._f.flush()

    def write(self, data: bytes) -> None:
        self._log('tx', data)
        self.inner.write(data)

    def read(self, size=4096, timeout=0.1) -> bytes:
        data = self.inner.read(size, timeout)
        self._log('rx', data)
        return data

    def set_baud(self, baud: int) -> None:
        self._f.write(json.dumps({'t': round(time.monotonic() - self._t0, 6), 'baud': baud}) + '\n')
        self.inner.set_baud(baud)

    def close(self) -> None:
        self._f.close()
        self.inner.close()


def list_ports():
    """[(device, description, is_ftdi)] for every serial port pyserial can see."""
    from serial.tools import list_ports as lp
    return [(p.device, p.description, p.vid == FTDI_VID) for p in lp.comports()]


def open_transport(port: str, baud: int = DEFAULT_BAUD, capture: str | None = None) -> Transport:
    t: Transport = FtdiTransport(port, baud) if port.startswith('ftdi://') else SerialTransport(port, baud)
    return RecordingTransport(t, capture) if capture else t
