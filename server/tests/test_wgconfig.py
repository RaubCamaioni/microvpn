from app.wgconfig import PRIVATE_KEY_PLACEHOLDER, PeerView, peers_hash, render_config

A = PeerView("alice-laptop", "A" * 43 + "=", "10.7.9.3", 20417)
B = PeerView("bob", "B" * 43 + "=", "10.7.9.4", 20005)


def test_render_matches_the_spec_exactly():
    assert render_config("10.7.9.2", [B, A], "raubcamaioni.com") == (
        "[Interface]\n"
        f"PrivateKey = {PRIVATE_KEY_PLACEHOLDER}\n"
        "Address = 10.7.9.2/24\n"
        "\n"
        "[Peer]\n"
        "# alice-laptop\n"
        f"PublicKey = {'A' * 43}=\n"
        "AllowedIPs = 10.7.9.3/32\n"
        "Endpoint = raubcamaioni.com:20417\n"
        "PersistentKeepalive = 25\n"
        "\n"
        "[Peer]\n"
        "# bob\n"
        f"PublicKey = {'B' * 43}=\n"
        "AllowedIPs = 10.7.9.4/32\n"
        "Endpoint = raubcamaioni.com:20005\n"
        "PersistentKeepalive = 25\n"
    )


def test_render_without_peers_has_only_the_interface():
    text = render_config("10.7.9.2", [], "h")
    assert text.endswith("Address = 10.7.9.2/24\n") and "[Peer]" not in text


def test_peers_hash_ignores_order_name_and_activity():
    assert peers_hash([A, B]) == peers_hash([B, PeerView("alice", A.public_key, A.address, A.port, 2)])
    assert peers_hash([A]) != peers_hash([A, B]) and len(peers_hash([A])) == 16
