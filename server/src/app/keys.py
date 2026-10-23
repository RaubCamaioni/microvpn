"""Random ids and tokens, and validation of what members send: public keys and names."""

import base64
import binascii
import re
import secrets

ROOM_ID_RE = re.compile(r"[a-z2-7]{26}")
PUBLIC_KEY_RE = re.compile(r"[A-Za-z0-9+/]{43}=")
ROOM_NAME_MAX = 40
DEVICE_NAME_MAX = 32


def new_room_id() -> str:
    """16 random bytes as lower-case base32 without padding: 26 characters, the room's only credential."""
    return base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=").lower()


def is_room_id(s: str) -> bool:
    return isinstance(s, str) and ROOM_ID_RE.fullmatch(s) is not None


def new_token() -> str:
    """16 random bytes as url-safe base64 without padding: 22 characters."""
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")


def is_public_key(s: str) -> bool:
    """A WireGuard public key: 44 base64 characters decoding to exactly 32 bytes."""
    if not isinstance(s, str) or PUBLIC_KEY_RE.fullmatch(s) is None:
        return False
    try:
        return len(base64.b64decode(s, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def _clean_name(s: str | None, limit: int, allow_empty: bool) -> str | None:
    if s is None:
        s = ""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return "" if allow_empty else None
    if len(s) > limit or not s.isprintable():
        return None
    return s


def clean_device_name(s: str | None) -> str | None:
    """The stripped device name, or None when empty, too long, or not printable (control characters)."""
    return _clean_name(s, DEVICE_NAME_MAX, allow_empty=False)


def clean_room_name(s: str | None) -> str | None:
    """The stripped room name ("" allowed), or None when too long or not printable."""
    return _clean_name(s, ROOM_NAME_MAX, allow_empty=True)


def short_id(room_id: str) -> str:
    """The first 8 characters: the config file name and interface tag `mv<short>`."""
    return room_id[:8]
