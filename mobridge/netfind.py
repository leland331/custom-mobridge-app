"""Find a network bridge: something on the LAN (any hardware running ser2net)
that shares the Mobridge's USB serial port as RFC2217.

1. Bridges advertise the DNS-SD service `_mobridge-bridge._tcp` (pi/mobridge-bridge.service
   for avahi). Browsed with python-zeroconf when it is installed.
2. Fallback: try TCP port 4000 on every address of the PC's own /24 subnet(s).

Discovery never sends a byte to the Mobridge: mDNS goes to the multicast group,
and the scan only opens and closes TCP connections (no RFC2217 session).
"""
from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor

SERVICE = '_mobridge-bridge._tcp.local.'
BRIDGE_PORT = 4000


def bridge_url(ip: str, port: int = BRIDGE_PORT) -> str:
    return 'rfc2217://%s:%d' % (ip, port)


def browse_mdns(wait_s: float = 1.5, cancel=None) -> list[tuple[str, str]]:
    """[(advertised name, url)] from DNS-SD; [] if zeroconf is missing or fails."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    found: dict[str, tuple[str, str]] = {}

    class L:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1000)
            if info is None:
                return
            for ip in info.parsed_addresses():
                if ':' not in ip:                  # IPv4 only
                    label = name[:-len(type_) - 1] if name.endswith(type_) else name
                    found[ip] = (label, bridge_url(ip, info.port or BRIDGE_PORT))

        update_service = add_service

        def remove_service(self, *a):
            pass

    try:
        zc = Zeroconf()
    except Exception:
        return []
    try:
        ServiceBrowser(zc, SERVICE, L())
        end = time.monotonic() + wait_s
        while time.monotonic() < end and not (cancel is not None and cancel.is_set()):
            time.sleep(0.05)
    finally:
        zc.close()
    return list(found.values())


def local_ipv4s() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:                       # address of the default route (a UDP connect sends nothing)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            u.connect(('10.255.255.255', 1))
            ips.add(u.getsockname()[0])
    except OSError:
        pass
    out = []
    for ip in sorted(ips):
        a = ipaddress.ip_address(ip)
        if a.is_private and not a.is_loopback and not a.is_link_local:
            out.append(ip)
    return out


def subnet_hosts() -> list[str]:
    hosts = []
    for ip in local_ipv4s():
        base = ip.rsplit('.', 1)[0]
        hosts += ['%s.%d' % (base, n) for n in range(1, 255) if '%s.%d' % (base, n) != ip]
    return hosts


def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True                            # closed at once; nothing sent
    except OSError:
        return False


def scan_ports(hosts=None, port: int = BRIDGE_PORT, timeout: float = 0.3,
               cancel=None, workers: int = 64) -> list[tuple[str, str]]:
    """[(ip, url)] for every host with `port` open."""
    hosts = subnet_hosts() if hosts is None else list(hosts)

    def probe(h):
        if cancel is not None and cancel.is_set():
            return False
        return _port_open(h, port, timeout)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        hits = list(ex.map(probe, hosts))
    return [(h, bridge_url(h, port)) for h, ok in zip(hosts, hits) if ok]


def find_bridges(cancel=None, hosts=None, port: int = BRIDGE_PORT, mdns: bool = True,
                 timeout: float = 0.3) -> list[tuple[str, str]]:
    """[(label, url)]: advertised bridges first, then scan hits not already listed."""
    out = browse_mdns(cancel=cancel) if mdns else []
    seen = {u for _n, u in out}
    if not (cancel is not None and cancel.is_set()):
        for ip, url in scan_ports(hosts, port, timeout, cancel):
            if url not in seen:
                out.append((ip, url)); seen.add(url)
    return out
