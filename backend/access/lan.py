"""This computer's addresses on the local network.

Used for the addresses shown when pairing a device and to recognise requests that come from this
computer itself through one of its own LAN addresses (they need no device login, like 127.0.0.1).
Nothing here sends a packet: the UDP "connect" only asks the system which interface it would use.
"""

from __future__ import annotations

import ipaddress
import platform
import socket
import subprocess
import time

_cache: dict[str, tuple[float, object]] = {}
TTL = 30.0


def _cached(key: str, fn):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < TTL:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def parse_ip(text: str | None):
    """An address object, with IPv4-mapped IPv6 (``::ffff:1.2.3.4``) turned into IPv4; else None."""
    try:
        ip = ipaddress.ip_address((text or "").strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _primary_ipv4() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # never sent; picks the interface of the default route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _addresses() -> list[str]:
    found: list[str] = []
    primary = _primary_ipv4()
    if primary:
        found.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for a in found:
        ip = parse_ip(a)
        if ip is None or ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            continue
        if ip.version == 6 and ip.is_link_local:
            continue  # fe80:: addresses need a zone id in URLs; not useful for pairing
        if str(ip) not in out:
            out.append(str(ip))
    return out


def own_addresses() -> list[str]:
    """This computer's non-loopback addresses, the one of the default route first (cached 30 s)."""
    return _cached("addresses", _addresses)


def _local_hostname() -> str | None:
    if platform.system() == "Darwin":
        try:
            name = subprocess.run(
                ["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=2
            ).stdout.strip()
            if name:
                return f"{name}.local"
        except (OSError, subprocess.SubprocessError):
            pass
    name = socket.gethostname().split(".")[0]
    return f"{name}.local" if name and name != "localhost" else None


def local_hostname() -> str | None:
    """The Bonjour name other devices on the network can use (``Lorenzos-MacBook.local``)."""
    return _cached("hostname", _local_hostname)


def is_this_computer(remote_addr: str | None) -> bool:
    """True for loopback and for this computer's own addresses."""
    ip = parse_ip(remote_addr)
    if ip is None:
        return False
    if ip.is_loopback:
        return True
    return str(ip) in own_addresses()


def is_ip_literal(host: str) -> bool:
    return parse_ip(host) is not None
