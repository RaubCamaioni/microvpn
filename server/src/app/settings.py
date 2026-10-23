"""Configuration from environment variables. Every value but DATABASE has a default (the spec's)."""

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    DATABASE: str
    # the URL prefix the app is served under behind the reverse proxy ("/microvpn"); "" at the root
    ROOT_PATH: str = ""
    # the public base URL of the app, used in room links and in join.sh
    PUBLIC_URL: str = "http://localhost:8080"
    # the host members put in their configs' Endpoint lines
    RELAY_HOST: str = "localhost"
    RELAY_BIND: str = "0.0.0.0"
    HTTP_BIND: str = "0.0.0.0:8080"
    RELAY_PORT_RANGE: tuple[int, int] = (20000, 21999)
    ROOM_MAX_MEMBERS: int = 8
    ROOM_MAX_LIVE: int = 64
    ROOM_CREATE_PER_IP_PER_HOUR: int = 3
    ROOM_MBPS: float = 50.0
    RELAY_TOTAL_MBPS: float = 200.0
    ROOM_LIFETIMES: tuple[int, ...] = (1, 4, 12, 24)
    SWEEP_SECONDS: float = 30.0
    # the proxies whose X-Forwarded-* / X-Real-IP headers uvicorn trusts (nginx on the Docker network)
    FORWARDED_ALLOW_IPS: str = "172.18.0.0/16"
    # the DB-IP city database the image bundles; blank locations when the file is missing
    GEOIP_DB: str = "/usr/share/dbip/dbip-city-lite.mmdb"

    @property
    def http_host(self) -> str:
        return self.HTTP_BIND.rsplit(":", 1)[0]

    @property
    def http_port(self) -> int:
        return int(self.HTTP_BIND.rsplit(":", 1)[1])

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "Settings":
        if not env.get("DATABASE"):
            raise ValueError("Missing environment variable: DATABASE")
        values: dict = {"DATABASE": env["DATABASE"]}

        def take(name, parse):
            if name in env:
                try:
                    values[name] = parse(env[name])
                except (TypeError, ValueError) as e:
                    raise ValueError(f"Malformed environment variable {name}={env[name]!r}: {e}") from e

        take("ROOT_PATH", lambda v: v.rstrip("/"))
        for name in ("PUBLIC_URL", "RELAY_HOST", "RELAY_BIND", "FORWARDED_ALLOW_IPS", "GEOIP_DB"):
            take(name, str)
        take("HTTP_BIND", _parse_bind)
        take("RELAY_PORT_RANGE", _parse_port_range)
        for name in ("ROOM_MAX_MEMBERS", "ROOM_MAX_LIVE", "ROOM_CREATE_PER_IP_PER_HOUR"):
            take(name, _parse_positive_int)
        for name in ("ROOM_MBPS", "RELAY_TOTAL_MBPS", "SWEEP_SECONDS"):
            take(name, _parse_positive_float)
        take("ROOM_LIFETIMES", _parse_lifetimes)
        return cls(**values)


def _parse_positive_int(v: str) -> int:
    n = int(v)
    if n <= 0:
        raise ValueError("must be positive")
    return n


def _parse_positive_float(v: str) -> float:
    n = float(v)
    if n <= 0:
        raise ValueError("must be positive")
    return n


def _parse_bind(v: str) -> str:
    host, _, port = v.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError("expected host:port")
    return v


def _parse_port_range(v: str) -> tuple[int, int]:
    lo, sep, hi = v.partition("-")
    if not sep:
        raise ValueError("expected lo-hi")
    lo_n, hi_n = int(lo), int(hi)
    if not (1 <= lo_n <= hi_n <= 65535):
        raise ValueError("expected 1 <= lo <= hi <= 65535")
    return lo_n, hi_n


def _parse_lifetimes(v: str) -> tuple[int, ...]:
    hours = tuple(_parse_positive_int(part.strip()) for part in v.split(",") if part.strip())
    if not hours:
        raise ValueError("expected a comma-separated list of hours")
    return hours


def settings_from_os() -> Settings:
    return Settings.from_environment(os.environ)
