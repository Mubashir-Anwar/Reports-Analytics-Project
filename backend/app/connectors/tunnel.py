"""SSH tunnels for data adapters (Advanced Connection Options → Proxy/SSH). Uses paramiko, which is already installed.

A tunnel listens on 127.0.0.1:<free port> in this process and forwards every connection through the SSH server to
the database host. The adapter's URL is rewritten to that local port (options.resolve). Tunnels are kept open and
shared by every report on the same adapter, and are opened again when the SSH connection drops.

The SSH server's host key is checked against the fingerprint saved with the adapter. A test without a saved
fingerprint answers with the server's fingerprint, and the person decides to trust it, as with `ssh` the first time.
"""
from __future__ import annotations

import base64
import hashlib
import io
import select
import socket
import threading
from typing import Any

from backend.app.connectors.errors import ConnectorQueryError
from backend.app.logs import LOG

CONNECT_TIMEOUT_SECONDS = 10


class UnknownHostKey(ConnectorQueryError):
    """The SSH server is reached, but its host key is not trusted yet."""

    def __init__(self, fingerprint: str):
        super().__init__(f"The SSH server's host key is not trusted yet: {fingerprint}")
        self.fingerprint = fingerprint


def fingerprint_of(key: Any) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def _private_key(text: str, passphrase: str):
    import paramiko  # noqa: PLC0415

    for kind in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return kind.from_private_key(io.StringIO(text), password=passphrase or None)
        except paramiko.PasswordRequiredException:
            raise ConnectorQueryError("The SSH private key needs its passphrase") from None
        except (paramiko.SSHException, ValueError):
            continue
    raise ConnectorQueryError("The SSH private key could not be read (OpenSSH or PEM, RSA / ECDSA / Ed25519)")


def _connect(ssh: dict[str, Any]):
    """An authenticated paramiko SSHClient, after checking the server's host key."""
    import paramiko  # noqa: PLC0415

    expected = str(ssh.get("hostKey") or "").strip()

    class _Check(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):  # noqa: ARG002 - paramiko's signature
            seen = fingerprint_of(key)
            if not expected:
                raise UnknownHostKey(seen)
            if seen != expected:
                raise ConnectorQueryError(f"The SSH server's host key changed ({seen}, expected {expected}): "
                                          "if the server was rebuilt, test the connection again and trust the new key")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_Check())
    key = _private_key(ssh["privateKey"], str(ssh.get("passphrase") or "")) if ssh.get("privateKey") else None
    try:
        client.connect(str(ssh["host"]), port=int(ssh.get("port") or 22), username=str(ssh.get("user") or ""),
                       password=None if key else (str(ssh.get("password") or "") or None), pkey=key,
                       timeout=CONNECT_TIMEOUT_SECONDS, banner_timeout=CONNECT_TIMEOUT_SECONDS,
                       auth_timeout=CONNECT_TIMEOUT_SECONDS, allow_agent=False, look_for_keys=False)
    except ConnectorQueryError:
        client.close()
        raise
    except paramiko.AuthenticationException:
        client.close()
        raise ConnectorQueryError("The SSH server refused the user name, password or key") from None
    except (paramiko.SSHException, OSError) as exc:
        client.close()
        raise ConnectorQueryError(f"Cannot reach the SSH server {ssh.get('host')}:{ssh.get('port') or 22}: {exc}") from None
    return client


class Tunnel:
    def __init__(self, ssh: dict[str, Any], remote_host: str, remote_port: int):
        self.remote = (remote_host, int(remote_port))
        self.client = _connect(ssh)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]
        self.closed = False
        threading.Thread(target=self._accept, name=f"ssh-tunnel-{self.port}", daemon=True).start()

    def alive(self) -> bool:
        transport = self.client.get_transport()
        return not self.closed and transport is not None and transport.is_active()

    def _accept(self) -> None:
        while not self.closed:
            try:
                local, peer = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._forward, args=(local, peer), daemon=True).start()

    def _forward(self, local: socket.socket, peer: tuple) -> None:
        try:
            channel = self.client.get_transport().open_channel("direct-tcpip", self.remote, peer)
        except Exception as exc:  # noqa: BLE001 - paramiko and socket errors
            LOG.warning("SSH tunnel to %s:%s could not open a channel: %s", *self.remote, exc)
            local.close()
            return
        try:
            while True:
                ready, _, _ = select.select([local, channel], [], [], 60)
                if local in ready:
                    data = local.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in ready:
                    data = channel.recv(65536)
                    if not data:
                        break
                    local.sendall(data)
        except OSError:
            pass
        finally:
            channel.close()
            local.close()

    def close(self) -> None:
        self.closed = True
        try:
            self.listener.close()
        finally:
            self.client.close()


_TUNNELS: dict[tuple, Tunnel] = {}
_LOCK = threading.Lock()


def _key(ssh: dict[str, Any], host: str, port: int) -> tuple:
    secret = hashlib.sha256(f"{ssh.get('password', '')}\0{ssh.get('privateKey', '')}\0{ssh.get('passphrase', '')}".encode()).hexdigest()
    return (str(ssh.get("host")), int(ssh.get("port") or 22), str(ssh.get("user")), str(ssh.get("hostKey") or ""), secret, host, int(port))


def local_port(ssh: dict[str, Any], host: str, port: int) -> int:
    """The local port of a working tunnel to host:port through this SSH server (opened when needed)."""
    key = _key(ssh, host, port)
    with _LOCK:
        tunnel = _TUNNELS.get(key)
        if tunnel is not None and not tunnel.alive():
            tunnel.close()
            tunnel = None
        if tunnel is None:
            tunnel = Tunnel(ssh, host, port)
            _TUNNELS[key] = tunnel
            LOG.info("SSH tunnel to %s:%s through %s opened on 127.0.0.1:%d", host, port, ssh.get("host"), tunnel.port)
        return tunnel.port


def check_host(ssh: dict[str, Any]) -> str:
    """Sign in to the SSH server once (a test); returns its host key fingerprint."""
    client = _connect(ssh)
    try:
        return fingerprint_of(client.get_transport().get_remote_server_key())
    finally:
        client.close()


def close_all() -> None:
    with _LOCK:
        for tunnel in _TUNNELS.values():
            tunnel.close()
        _TUNNELS.clear()
