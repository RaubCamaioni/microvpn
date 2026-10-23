import base64
import re

from app import keys


def test_room_ids_are_26_lowercase_base32_and_unique():
    ids = {keys.new_room_id() for _ in range(50)}
    assert len(ids) == 50 and all(re.fullmatch(r"[a-z2-7]{26}", i) for i in ids)
    assert keys.is_room_id(next(iter(ids))) and not keys.is_room_id("ABC") and not keys.is_room_id("a" * 26 + "a")


def test_tokens_are_22_urlsafe_chars():
    t = keys.new_token()
    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", t) and t != keys.new_token()


def test_public_key_accepts_a_real_key():
    assert keys.is_public_key(base64.b64encode(bytes(range(32))).decode())


def test_public_key_rejects_wrong_length_and_whitespace():
    assert not keys.is_public_key(base64.b64encode(bytes(31)).decode())
    assert not keys.is_public_key(base64.b64encode(bytes(33)).decode())
    assert not keys.is_public_key(" " + base64.b64encode(bytes(32)).decode())
    assert not keys.is_public_key(base64.b64encode(bytes(32)).decode() + "\n")
    assert not keys.is_public_key("not base64 at all!!!!!!!!!!!!!!!!!!!!!!!!!!!")


def test_device_names():
    assert keys.clean_device_name("  rob-laptop ") == "rob-laptop"
    assert keys.clean_device_name("x" * 32) == "x" * 32
    assert keys.clean_device_name("x" * 33) is None and keys.clean_device_name("   ") is None
    assert keys.clean_device_name("a\nb") is None and keys.clean_device_name("a\x00b") is None


def test_room_names():
    assert keys.clean_room_name(None) == "" and keys.clean_room_name(" game night ") == "game night"
    assert keys.clean_room_name("x" * 41) is None and keys.clean_room_name("a\tb") is None


def test_short_id():
    assert keys.short_id("abcdefghijklmnopqrstuvwxyz") == "abcdefgh"
