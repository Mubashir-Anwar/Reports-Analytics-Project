"""Advanced Connection Options: certificate files and SSH tunnels of data adapters (connectors/options.py, tunnel.py)."""
from __future__ import annotations

import base64
import os
import select
import socket
import sys
import threading
from pathlib import Path

import paramiko
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.connectors import options, sql as sql_connector, tunnel  # noqa: E402
from backend.app.connectors.errors import ConnectorQueryError  # noqa: E402
from test_workspace_and_server import client, token  # noqa: E402,F401

PEM = b"-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"
KEY = b"-----BEGIN PRIVATE KEY-----\nMIIEfake\n-----END PRIVATE KEY-----\n"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def query_of(url: str) -> dict[str, str]:
    from urllib.parse import parse_qsl  # noqa: PLC0415
    return dict(parse_qsl(url.partition("?")[2]))


# ---------------------------------------------------------------- checking what the wizard sends
def test_files_are_checked_kept_and_removed():
    saved = options.clean("mongodb", {"files": {"ca": {"name": "../../ca.pem", "data": b64(PEM)}}}, None)
    assert saved["files"]["ca"]["name"] == "ca.pem"                       # no folders in a file name
    assert options.clean("mongodb", {"files": {"ca": "keep"}}, saved) == saved
    assert options.clean("mongodb", None, saved) == saved                  # an older page: keep them
    assert options.clean("mongodb", {"files": {"ca": None}}, saved) == {}
    with pytest.raises(ValueError, match="PEM"):
        options.clean("mongodb", {"files": {"ca": {"name": "x", "data": b64(b"not a pem")}}}, None)
    with pytest.raises(ValueError, match="KB"):
        options.clean("mongodb", {"files": {"ca": {"name": "x", "data": b64(b"-----BEGIN" + b"x" * 70000)}}}, None)
    with pytest.raises(ValueError, match="needs its client certificate"):
        options.clean("mongodb", {"files": {"key": {"name": "k", "data": b64(KEY)}}}, None)
    with pytest.raises(ValueError, match="no certificate files"):
        options.clean("sqlserver", {"files": {"ca": {"name": "x", "data": b64(PEM)}}}, None)
    with pytest.raises(ValueError, match="Unknown TLS file"):
        options.clean("mongodb", {"files": {"other": None}}, None)


def test_ssh_secrets_stay_for_the_same_server_only():
    first = options.clean("postgresql", {"ssh": {"host": "bastion", "user": "ops", "password": "pw-1"}}, None)
    assert first["ssh"] == {"host": "bastion", "port": 22, "user": "ops", "password": "pw-1"}
    again = options.clean("postgresql", {"ssh": {"host": "bastion", "user": "ops", "password": "", "hostKey": "SHA256:" + "a" * 43}}, first)
    assert again["ssh"]["password"] == "pw-1" and again["ssh"]["hostKey"].startswith("SHA256:")
    with pytest.raises(ValueError, match="SSH password"):
        options.clean("postgresql", {"ssh": {"host": "other-host", "user": "ops"}}, first)   # another server: no saved secret
    with pytest.raises(ValueError, match="private key"):
        options.clean("postgresql", {"ssh": {"host": "bastion", "user": "ops", "auth": "key", "privateKey": "x"}}, None)
    with pytest.raises(ValueError, match="cannot go through an SSH tunnel"):
        options.clean("files", {"ssh": {"host": "b", "user": "u", "password": "p"}}, None)
    assert options.clean("postgresql", {"ssh": None}, first) == {}
    shown = options.summary(again)
    assert shown["ssh"] == {"host": "bastion", "port": 22, "user": "ops", "auth": "password", "hostKey": again["ssh"]["hostKey"]}
    assert "pw-1" not in str(shown)


# ---------------------------------------------------------------- applying them to the URL
def test_mongodb_files_go_into_the_url():
    extra = options.clean("mongodb", {"files": {"ca": {"name": "ca.pem", "data": b64(PEM)}, "cert": {"name": "c.pem", "data": b64(PEM)},
                                                "key": {"name": "k.pem", "data": b64(KEY)}}}, None)
    url = options.resolve("mongodb", "mongodb://u:p@h1:27017,h2/db?readPreferenceTags=dc:ny,rack:1", extra)
    assert "readPreferenceTags=dc:ny,rack:1" in url                     # other options are kept exactly as written
    found = query_of(url)
    assert found["tls"] == "true"
    assert Path(found["tlsCAFile"]).read_bytes() == PEM
    assert Path(found["tlsCertificateKeyFile"]).read_bytes() == PEM.rstrip() + b"\n" + KEY   # cert + key in one file
    from pymongo.uri_parser import parse_uri  # noqa: PLC0415
    parsed = parse_uri(url)["options"]
    assert parsed["readPreferenceTags"] == [{"dc": "ny", "rack": "1"}] and parsed["tlsCAFile"] == found["tlsCAFile"]
    assert options.resolve("mongodb", "mongodb://h", extra).startswith("mongodb://h/?")   # a "/" before the options


def test_postgres_and_mysql_files(monkeypatch):
    extra = options.clean("postgresql", {"files": {"ca": {"name": "ca.pem", "data": b64(PEM)}, "key": {"name": "k.pem", "data": b64(KEY)}}}, None)
    found = query_of(options.resolve("postgresql", "postgresql://u:p@db/shop?sslmode=verify-full", extra))
    assert found["sslmode"] == "verify-full" and Path(found["sslrootcert"]).read_bytes() == PEM and Path(found["sslkey"]).read_bytes() == KEY

    seen = {}

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def execute(self, *_): return None

    class FakeConnection:
        def cursor(self): return FakeCursor()

    def connect(**kwargs):
        seen.update(kwargs)
        return FakeConnection()
    import pymysql  # noqa: PLC0415
    monkeypatch.setattr(pymysql, "connect", connect)
    url = options.resolve("mysql", "mysql://u:p@db:3306/shop", options.clean("mysql", {"files": {"ca": {"name": "ca.pem", "data": b64(PEM)}}}, None))
    sql_connector._Session("mysql", url, 5000)
    assert Path(seen["ssl"]["ca"]).read_bytes() == PEM
    sql_connector._Session("mysql", "mysql://u:p@db:3306/shop?ssl=true", 5000)
    assert seen["ssl"] == {}


def test_no_options_leave_the_url_alone():
    assert options.resolve("postgresql", "postgresql://u:p@db/shop", {}) == "postgresql://u:p@db/shop"
    with pytest.raises(ConnectorQueryError, match="mongodb\\+srv"):
        options.resolve("mongodb", "mongodb+srv://u:p@cluster.example.net/db", {"ssh": {"host": "b", "user": "u", "password": "p"}})


# ---------------------------------------------------------------- a real SSH tunnel to a local echo server
class _Server(paramiko.ServerInterface):
    def __init__(self):
        self.targets: list[tuple[str, int]] = []

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("ops", "ssh-pw") else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.targets.append(destination)
        return paramiko.OPEN_SUCCEEDED


def _pump(channel, target):
    remote = socket.create_connection(target)
    while True:
        ready, _, _ = select.select([channel, remote], [], [], 5)
        if not ready:
            break
        source, sink = (channel, remote) if channel in ready else (remote, channel)
        data = source.recv(4096)
        if not data:
            break
        sink.sendall(data)
    remote.close()
    channel.close()


@pytest.fixture()
def ssh_server():
    host_key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    server = _Server()

    def serve():
        while True:
            try:
                sock, _ = listener.accept()
            except OSError:
                return
            def channels(sock=sock):
                transport = paramiko.Transport(sock)
                transport.add_server_key(host_key)
                try:
                    transport.start_server(server=server)
                except (paramiko.SSHException, OSError):
                    return                     # the client did not trust the key and left
                while transport.is_active():
                    channel = transport.accept(1)
                    if channel is not None:
                        threading.Thread(target=_pump, args=(channel, server.targets[-1]), daemon=True).start()
            threading.Thread(target=channels, daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()

    echo = socket.socket()
    echo.bind(("127.0.0.1", 0))
    echo.listen(5)

    def answer():
        while True:
            try:
                sock, _ = echo.accept()
            except OSError:
                return
            sock.sendall(b"hello " + sock.recv(100))
            sock.close()
    threading.Thread(target=answer, daemon=True).start()
    yield {"port": listener.getsockname()[1], "fingerprint": tunnel.fingerprint_of(host_key), "echo": echo.getsockname()[1], "server": server}
    tunnel.close_all()
    listener.close()
    echo.close()


def test_tunnel_checks_the_host_key_and_forwards(ssh_server):
    ssh = {"host": "127.0.0.1", "port": ssh_server["port"], "user": "ops", "password": "ssh-pw"}
    with pytest.raises(tunnel.UnknownHostKey) as unknown:
        tunnel.local_port(ssh, "127.0.0.1", ssh_server["echo"])
    assert unknown.value.fingerprint == ssh_server["fingerprint"]
    with pytest.raises(ConnectorQueryError, match="host key changed"):
        tunnel.local_port({**ssh, "hostKey": "SHA256:" + "A" * 43}, "127.0.0.1", ssh_server["echo"])
    with pytest.raises(ConnectorQueryError, match="refused"):
        tunnel.local_port({**ssh, "password": "wrong", "hostKey": ssh_server["fingerprint"]}, "127.0.0.1", ssh_server["echo"])

    trusted = {**ssh, "hostKey": ssh_server["fingerprint"]}
    port = tunnel.local_port(trusted, "127.0.0.1", ssh_server["echo"])
    assert tunnel.local_port(trusted, "127.0.0.1", ssh_server["echo"]) == port      # one tunnel, shared
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(b"db")
        assert sock.recv(100) == b"hello db"
    url = options.resolve("postgresql", f"postgresql://u:p@127.0.0.1:{ssh_server['echo']}/shop", {"ssh": trusted})
    assert url == f"postgresql://u:p@127.0.0.1:{port}/shop"
    mongo = options.resolve("mongodb", f"mongodb://u:p@127.0.0.1:{ssh_server['echo']},other:27017/db?appName=x", {"ssh": trusted})
    assert mongo == f"mongodb://u:p@127.0.0.1:{port}/db?appName=x&directConnection=true"


def test_ssh_through_the_api(client, ssh_server, monkeypatch):  # noqa: F811
    tested = []
    monkeypatch.setattr(sql_connector, "test", lambda kind, url: tested.append(url) or {"ok": True, "message": "Connected"})
    admin = token("admin-token-for-tests-0123456789")
    url = f"postgresql://report:pw-123@127.0.0.1:{ssh_server['echo']}/shop"
    ssh = {"host": "127.0.0.1", "port": ssh_server["port"], "user": "ops", "password": "ssh-pw"}
    first = client.post("/api/admin/connections/test", headers=admin, json={"type": "postgresql", "uri": url, "options": {"ssh": ssh}}).json()
    assert first["status"] == "hostKey" and first["fingerprint"] == ssh_server["fingerprint"] and not tested
    ssh["hostKey"] = first["fingerprint"]
    second = client.post("/api/admin/connections/test", headers=admin, json={"type": "postgresql", "uri": url, "options": {"ssh": ssh}}).json()
    assert second["ok"] and "through the SSH server" in second["message"] and "127.0.0.1:" in tested[-1]
    assert f":{ssh_server['echo']}/" not in tested[-1]                         # the driver is sent to the tunnel

    files = {"ca": {"name": "ca.pem", "data": b64(PEM)}}
    saved = client.post("/api/admin/connections", headers=admin, json={"type": "postgresql", "name": "Tunnel PG", "uri": url,
                                                                       "options": {"ssh": ssh, "files": files}})
    assert saved.status_code == 200, saved.text
    listed = {item["id"]: item for item in client.get("/api/admin/connections", headers=admin).json()["connections"]}
    assert listed["tunnel_pg"]["options"]["ssh"]["host"] == "127.0.0.1" and listed["tunnel_pg"]["options"]["files"] == {"ca": "ca.pem"}
    assert "ssh-pw" not in str(listed) and "MIIB" not in str(listed)

    # Saved again without options (or with an empty password): the saved SSH sign-in and file stay.
    again = client.post("/api/admin/connections", headers=admin, json={"type": "postgresql", "id": "tunnel_pg", "name": "Tunnel PG", "uri": url,
                                                                       "options": {"ssh": {**ssh, "password": ""}}})
    assert again.status_code == 200, again.text
    assert client.post("/api/admin/connections/tunnel_pg/test", headers=admin).json()["ok"]
    assert "sslrootcert=" in tested[-1] and os.path.exists(query_of(tested[-1])["sslrootcert"])
    bad = client.post("/api/admin/connections/test", headers=admin, json={"type": "postgresql", "uri": url, "options": {"files": {"ca": {"name": "x", "data": "!!"}}}})
    assert bad.status_code == 422
