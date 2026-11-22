import pytest

from app.settings import Settings


def test_defaults_match_the_spec():
    s = Settings.from_environment({"DATABASE": "x.db"})
    assert (s.ROOM_MAX_MEMBERS, s.ROOM_MAX_LIVE, s.ROOM_CREATE_PER_IP_PER_HOUR) == (8, 64, 3)
    assert s.ROOM_JOIN_PER_IP_PER_HOUR == 60
    assert (s.ROOM_MBPS, s.RELAY_TOTAL_MBPS) == (50, 200)
    assert s.ROOM_LIFETIMES == (1, 4, 12, 24) and s.RELAY_PORT_RANGE == (20000, 21999)
    assert (s.http_host, s.http_port) == ("0.0.0.0", 8080) and s.ROOT_PATH == ""


def test_parses_ranges_lists_and_strips_root_path():
    s = Settings.from_environment(
        {
            "DATABASE": "x.db",
            "RELAY_PORT_RANGE": "100-105",
            "ROOM_LIFETIMES": "2,6",
            "ROOT_PATH": "/microvpn/",
            "HTTP_BIND": "127.0.0.1:9000",
        }
    )
    assert s.RELAY_PORT_RANGE == (100, 105) and s.ROOM_LIFETIMES == (2, 6)
    assert s.ROOT_PATH == "/microvpn" and (s.http_host, s.http_port) == ("127.0.0.1", 9000)


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"DATABASE": "x", "RELAY_PORT_RANGE": "5"},
        {"DATABASE": "x", "ROOM_LIFETIMES": "a"},
        {"DATABASE": "x", "RELAY_PORT_RANGE": "9-3"},
    ],
)
def test_missing_or_malformed_values_raise(env):
    with pytest.raises(ValueError):
        Settings.from_environment(env)


def test_account_settings():
    s = Settings.from_environment(
        {
            "DATABASE": "x.db",
            "GITHUB_CLIENT_ID": "id",
            "GITHUB_CLIENT_SECRET": "sec",
            "ADMIN_GITHUB_IDS": "12, 34,",
            "PERMANENT_ROOMS_PER_ACCOUNT": "5",
        }
    )
    assert s.github_enabled and s.ADMIN_GITHUB_IDS == frozenset({12, 34}) and s.PERMANENT_ROOMS_PER_ACCOUNT == 5
    d = Settings.from_environment({"DATABASE": "x.db"})
    assert not d.github_enabled and d.ADMIN_GITHUB_IDS == frozenset() and d.PERMANENT_ROOMS_PER_ACCOUNT == 3
    # the login needs both halves of the OAuth app
    assert not Settings.from_environment({"DATABASE": "x.db", "GITHUB_CLIENT_ID": "id"}).github_enabled
    with pytest.raises(ValueError, match="ADMIN_GITHUB_IDS"):
        Settings.from_environment({"DATABASE": "x.db", "ADMIN_GITHUB_IDS": "raub"})
