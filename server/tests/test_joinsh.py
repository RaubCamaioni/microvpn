import os
import shutil
import stat
import subprocess

import pytest


def test_join_script_is_served_for_a_live_room_only(client):
    room = client.post("/rooms", json={"name": "", "lifetime_hours": 1}).json()
    r = client.get(f"/r/{room['id']}/join.sh")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-shellscript")
    assert r.text.startswith("#!/bin/sh\n") and f"/r/{room['id']}" in r.text and f"mv{room['id'][:8]}" in r.text
    assert "wg genkey" in r.text and "__PRIVATE_KEY__" in r.text and "wg-quick up" in r.text
    assert client.get("/r/" + "z" * 26 + "/join.sh").status_code == 404


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_join_script_runs_with_stub_wg_and_curl(client, tmp_path):
    room = client.post("/rooms", json={"name": "", "lifetime_hours": 1}).json()
    script = client.get(f"/r/{room['id']}/join.sh").text
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "wg").write_text('#!/bin/sh\ncase "$1" in genkey) echo PRIVKEY;; pubkey) echo PUBKEY;; esac\n')
    # the curl stub answers the join with a token and the config fetch with a template, and records its calls
    (bin_dir / "curl").write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$LOG"\n'
        'case "$*" in\n'
        "  *members*) echo '{\"member_token\":\"TOK\",\"address\":\"10.1.1.2\"}';;\n"
        "  *me.conf*) printf '[Interface]\\nPrivateKey = __PRIVATE_KEY__\\nAddress = 10.1.1.2/24\\n\\n[Peer]\\nPublicKey = X\\n';;\n"
        "esac\n"
    )
    for f in ("wg", "curl"):
        os.chmod(bin_dir / f, (bin_dir / f).stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "work"
    work.mkdir()
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "LOG": str(tmp_path / "calls.log")}
    out = subprocess.run(["sh", "-s", "--", "my box"], input=script, text=True, capture_output=True, cwd=work, env=env)
    assert out.returncode == 0, out.stderr
    tag = f"mv{room['id'][:8]}"
    assert (work / f"{tag}.key").read_text().strip() == "PRIVKEY"
    assert (work / f"{tag}.token").read_text().strip() == "TOK"
    conf = (work / f"{tag}.conf").read_text()
    assert "PrivateKey = PRIVKEY" in conf and "__PRIVATE_KEY__" not in conf
    assert stat.S_IMODE((work / f"{tag}.conf").stat().st_mode) == 0o600
    assert "(1 peers)" in out.stdout and f"wg-quick up ./{tag}.conf" in out.stdout
    calls = (tmp_path / "calls.log").read_text()
    assert '"name":"my box"' in calls and '"public_key":"PUBKEY"' in calls and "Bearer TOK" in calls
    # a second run reuses the key and token: no new POST
    out2 = subprocess.run(["sh", "-s", "--", "my box"], input=script, text=True, capture_output=True, cwd=work, env=env)
    assert out2.returncode == 0 and (tmp_path / "calls.log").read_text().count("members") == 1
