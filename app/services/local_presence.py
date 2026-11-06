"""
Local-presence evidence for pairing auto-approve (the owner's rule,
2026-11-05): "if device is on Bluetooth or same wifi and it sees it
on the list in xod it can just approve it."

This module answers one narrow question about an HTTP request that
carried a phone enrollment advertisement (services/phone_enroll.py):
did it arrive from this local network, as observed by THIS Xod?
Evidence is observed by the transport, never claimed by the
advertisement itself — a signed advertisement already proves who is
asking (record signature + the GitHub sign-in assertion, the pairing
gate); presence only replaces the owner's approval tap for a device
that is demonstrably here.

Kinds:
  - "same_wifi": the source address is an RFC1918 address inside a
    subnet one of this host's own interfaces sits on (address +
    netmask from psutil, a pinned dependency). Same LAN, in practice
    the same Wi-Fi. Tailnet CGNAT (100.64.0.0/10), link-local,
    public, and merely-private-but-off-subnet addresses are NOT
    evidence. A VPN that terminates inside the LAN presents as LAN;
    that is the owner's own tunnel, documented rather than hidden.
  - "loopback": the source is this very machine (a sibling backend
    enrolling with this one). Physically present by definition,
    labelled as its own kind so the audit never dresses it up as
    Wi-Fi.

Bluetooth is deliberately NOT an evidence kind here: the desktop's
BLE surface (services/phone_ble.py) observes only an anonymous
service UUID and a user-set device name, and Android rotates BLE
addresses — nothing binds a BLE sighting to a signed advertisement's
device id or key, so approving on it would be simulating evidence.
See docs/HANDOFF-1.0.md follow-ups / the 2026-11-05 build log.
"""
import ipaddress
import socket
from typing import Any, Dict, List, Optional

_TAILNET = ipaddress.ip_network("100.64.0.0/10")   # CGNAT: Tailscale et al — reachable, not local
_RFC1918 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


def local_networks() -> List[ipaddress.IPv4Network]:
    """The subnets this host itself sits on, from its interfaces.
    Falls back to /24s around the hostname's private addresses when
    psutil cannot enumerate netmasks."""
    nets: List[ipaddress.IPv4Network] = []
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and a.address and a.netmask:
                    try:
                        nets.append(ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False))
                    except ValueError:
                        pass
    except Exception:
        nets = []
    if not nets:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                addr = info[4][0]
                try:
                    ip = ipaddress.ip_address(addr)
                except ValueError:
                    continue
                if any(ip in n for n in _RFC1918):
                    nets.append(ipaddress.ip_network(f"{addr}/24", strict=False))
        except Exception:
            pass
    # widest first so a specific match reports the tightest subnet containing it below
    return sorted(set(nets), key=lambda n: n.prefixlen)


def _is_lan_private(ip: ipaddress.IPv4Address) -> bool:
    return any(ip in n for n in _RFC1918)


def classify(source_ip: Optional[str],
             networks: Optional[List[ipaddress.IPv4Network]] = None) -> Optional[Dict[str, Any]]:
    """{"kind", "source", "detail"} when the source is local-presence
    evidence, else None. `networks` is injectable for tests."""
    if not source_ip:
        return None
    try:
        ip = ipaddress.ip_address(str(source_ip).strip())
    except ValueError:
        return None
    if ip.is_loopback:
        return {"kind": "loopback", "source": str(ip),
                "detail": "the request arrived from this same computer"}
    if not isinstance(ip, ipaddress.IPv4Address):
        return None
    if ip in _TAILNET or ip.is_link_local or not _is_lan_private(ip):
        return None
    for net in (networks if networks is not None else local_networks()):
        if ip in net and _is_lan_private(net.network_address):
            return {"kind": "same_wifi", "source": str(ip),
                    "detail": f"the request arrived from {ip}, on this computer's local network {net}"}
    return None
