"""Rendering a member's WireGuard configuration.

The private key never reaches the server, so the text carries a placeholder the member's device
(browser or join.sh) substitutes before use.
"""

import hashlib
from dataclasses import dataclass
from typing import Sequence

PRIVATE_KEY_PLACEHOLDER = "__PRIVATE_KEY__"
KEEPALIVE_SECONDS = 25


@dataclass(frozen=True)
class PeerView:
    name: str
    public_key: str
    address: str
    port: int
    slots_active: int = 0


def render_config(address: str, peers: Sequence[PeerView], relay_host: str) -> str:
    """The config for the member at `address`, listing every peer through its pair's relay port."""
    lines = [
        "[Interface]",
        f"PrivateKey = {PRIVATE_KEY_PLACEHOLDER}",
        f"Address = {address}/24",
    ]
    for peer in sorted(peers, key=lambda p: (p.name, p.public_key)):
        lines += [
            "",
            "[Peer]",
            f"# {peer.name}",
            f"PublicKey = {peer.public_key}",
            f"AllowedIPs = {peer.address}/32",
            f"Endpoint = {relay_host}:{peer.port}",
            f"PersistentKeepalive = {KEEPALIVE_SECONDS}",
        ]
    return "\n".join(lines) + "\n"


def peers_hash(peers: Sequence[PeerView]) -> str:
    """Identifies the set of peers a config covers; the page marks a download stale when it changes.
    Order, names and relay activity do not count: only who is reachable, where, through which port."""
    digest = hashlib.sha256("\n".join(sorted(f"{p.public_key} {p.address} {p.port}" for p in peers)).encode())
    return digest.hexdigest()[:16]
