"""Approximate location of an IP address, from the DB-IP "IP to City Lite" database bundled into the
image (CC BY 4.0, https://db-ip.com). Offline on purpose: a member's address never goes to a third
party. Shown to a room's admin beside each device waiting to be accepted."""

import ipaddress
import logging
import os

logger = logging.getLogger("uvicorn.error")


def is_private(ip: str) -> bool:
    """Loopback, link-local, RFC 1918 and unique-local addresses: 'local network', not a place."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


class Geo:
    def __init__(self, path: str):
        self.path = path
        self._reader = None
        if path and os.path.exists(path):
            try:
                import maxminddb

                self._reader = maxminddb.open_database(path)
            except Exception as e:  # noqa: BLE001 - a broken file must not stop the app
                logger.warning("geo: cannot open %s: %s", path, e)
        else:
            logger.warning("geo: no database at %s; locations will be blank", path)

    @property
    def available(self) -> bool:
        return self._reader is not None

    def locate(self, ip: str) -> str:
        """'City, Region, Country' with whatever parts the database has; '' when unknown."""
        if not ip:
            return ""
        if is_private(ip):
            return "local network"
        if self._reader is None:
            return ""
        try:
            record = self._reader.get(ip)
        except (ValueError, TypeError):
            return ""
        if not record:
            return ""
        parts = []
        city = (record.get("city") or {}).get("names", {}).get("en")
        subdivisions = record.get("subdivisions") or []
        region = subdivisions[0].get("names", {}).get("en") if subdivisions else None
        country = (record.get("country") or {}).get("names", {}).get("en")
        for part in (city, region, country):
            if part and part not in parts:
                parts.append(part)
        return ", ".join(parts)
