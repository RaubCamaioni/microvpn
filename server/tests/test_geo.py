import os

import pytest

from app.geo import Geo, is_private


def test_private_and_odd_addresses():
    assert is_private("10.1.2.3") and is_private("192.168.0.5") and is_private("127.0.0.1") and is_private("fd00::1")
    assert not is_private("8.8.8.8") and not is_private("2606:4700::1111")
    assert not is_private("not an ip")


def test_geo_without_a_database_is_quiet():
    g = Geo("/nonexistent/file.mmdb")
    assert g.available is False and g.locate("8.8.8.8") == "" and g.locate("10.0.0.1") == "local network"


@pytest.mark.skipif(not os.path.exists("/usr/share/dbip/dbip-city-lite.mmdb"), reason="needs the DB-IP file of the image")
def test_geo_with_the_bundled_database():
    g = Geo("/usr/share/dbip/dbip-city-lite.mmdb")
    assert g.available
    place = g.locate("8.8.8.8")
    assert place and "United States" in place
    assert g.locate("10.0.0.1") == "local network" and g.locate("garbage") == ""
