"""Advanced Connection Options of a data adapter that do not fit in its URL: TLS certificate files and an SSH tunnel.

Everything else (authentication, TLS on/off, replica set, read preference, timeouts, ...) is part of the URL, and the
adapter wizard builds that URL from its tabs. What is kept here is saved encrypted with the URL in the private settings
(`mongo_connections[id]["extra"]`):

    {"files": {"ca": {"name": "ca.pem", "data": "<base64>"}, "cert": {...}, "key": {...}},
     "ssh":   {"host": "bastion", "port": 22, "user": "ops", "password": "...", "privateKey": "...", "passphrase": "...",
               "hostKey": "SHA256:..."}}

`resolve(kind, url, extra)` is called only where a connection is opened: it writes the files to a private temporary
folder of this process (removed at exit), adds their paths to the URL with the driver's own option names, and points
the URL at a local SSH tunnel.
"""
from __future__ import annotations

import atexit
import base64
import binascii
import hashlib
import os
import re
import shutil
import tempfile
import threading
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from backend.app.connectors import tunnel
from backend.app.connectors.errors import ConnectorQueryError

MAX_FILE_BYTES = 64 * 1024
FILE_ROLES = ("ca", "cert", "key")

# The driver's own URL option for each file.
FILE_OPTIONS: dict[str, dict[str, str]] = {
    "mongodb": {"ca": "tlsCAFile", "cert": "tlsCertificateKeyFile"},           # cert + key go in one PEM file
    "postgresql": {"ca": "sslrootcert", "cert": "sslcert", "key": "sslkey"},
    "mysql": {"ca": "ssl_ca", "cert": "ssl_cert", "key": "ssl_key"},
    "mariadb": {"ca": "ssl_ca", "cert": "ssl_cert", "key": "ssl_key"},
    "redis": {"ca": "ssl_ca_certs", "cert": "ssl_certfile", "key": "ssl_keyfile"},
}
# Kinds that reach one host:port and can go through an SSH tunnel.
SSH_KINDS = {"mongodb", "postgresql", "mysql", "mariadb", "sqlserver", "oracle", "redis", "clickhouse", "elasticsearch"}
SSH_SECRETS = ("password", "privateKey", "passphrase")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,253}$")

_FOLDER: str | None = None
_FOLDER_LOCK = threading.Lock()


def _folder() -> str:
    global _FOLDER
    with _FOLDER_LOCK:
        if _FOLDER is None or not os.path.isdir(_FOLDER):
            _FOLDER = tempfile.mkdtemp(prefix="rs-tls-")   # 0700, only this process's user can read it
            atexit.register(shutil.rmtree, _FOLDER, True)
        return _FOLDER


def _write(data: bytes, suffix: str = ".pem") -> str:
    path = os.path.join(_folder(), hashlib.sha256(data).hexdigest()[:24] + suffix)
    if not os.path.exists(path):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    return path


def _file_bytes(item: Any) -> bytes:
    return base64.b64decode(str((item or {}).get("data", "")))


# ----------------------------------------------------------------- checking what the wizard sends
def _clean_file(role: str, item: Any) -> dict[str, str]:
    if not isinstance(item, dict):
        raise ValueError(f"Send the {role} file as {{name, data}}")
    try:
        data = base64.b64decode(str(item.get("data", "")), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"The {role} file is not valid base64") from None
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError(f"The {role} file must be between 1 byte and {MAX_FILE_BYTES // 1024} KB")
    if b"-----BEGIN" not in data:
        raise ValueError(f"The {role} file must be a PEM text file (it starts with -----BEGIN ...)")
    name = os.path.basename(str(item.get("name") or f"{role}.pem"))[:120]
    return {"name": name, "data": base64.b64encode(data).decode()}


def _clean_ssh(raw: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    host = str(raw.get("host", "")).strip()
    if not HOST_PATTERN.fullmatch(host):
        raise ValueError("Give the SSH server's host name or IP address")
    try:
        port = int(raw.get("port") or 22)
    except (TypeError, ValueError):
        raise ValueError("The SSH port must be a number") from None
    if not 1 <= port <= 65535:
        raise ValueError("The SSH port must be between 1 and 65535")
    user = str(raw.get("user", "")).strip()
    if not user:
        raise ValueError("Give the SSH user name")
    auth = str(raw.get("auth") or ("key" if raw.get("privateKey") else "password"))
    if auth not in ("password", "key"):
        raise ValueError("SSH sign-in is a password or a private key")
    same_server = saved and saved.get("host") == host and int(saved.get("port") or 22) == port and saved.get("user") == user
    result: dict[str, Any] = {"host": host, "port": port, "user": user}
    # A secret left empty keeps the saved one (the wizard never shows saved secrets again).
    wanted = ("password",) if auth == "password" else ("privateKey", "passphrase")
    for field in wanted:
        value = str(raw.get(field) or "")
        if not value and same_server and (saved.get("password") if auth == "password" else saved.get("privateKey")):
            value = str(saved.get(field) or "")
        if value:
            result[field] = value[:16384]
    if auth == "password" and not result.get("password"):
        raise ValueError("Give the SSH password")
    if auth == "key" and "PRIVATE KEY" not in result.get("privateKey", ""):
        raise ValueError("Give the SSH private key (the text of an OpenSSH or PEM key file)")
    host_key = str(raw.get("hostKey") or (saved.get("hostKey") if same_server else "") or "").strip()
    if host_key and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", host_key):
        raise ValueError("The trusted SSH host key must look like SHA256:...")
    if host_key:
        result["hostKey"] = host_key
    return result


def clean(kind: str, raw: Any, saved: dict[str, Any] | None) -> dict[str, Any]:
    """The options to save: what the wizard sent, on top of the saved ones.

    files[role]: an object replaces the file, null removes it, absent keeps the saved one.
    ssh: an object replaces it (empty secrets keep the saved ones for the same server), null removes it, absent keeps it.
    """
    saved = saved if isinstance(saved, dict) else {}
    if raw is None:
        return dict(saved)
    if not isinstance(raw, dict):
        raise ValueError("Send the connection options as an object")
    files = dict(saved.get("files") or {})
    if "files" in raw:
        sent = raw["files"]
        if not isinstance(sent, dict):
            raise ValueError("Send the TLS files as an object")
        for role, item in sent.items():
            if role not in FILE_ROLES:
                raise ValueError(f"Unknown TLS file: {role}")
            if item is None:
                files.pop(role, None)
            elif item != "keep":
                files[role] = _clean_file(role, item)
    allowed = FILE_OPTIONS.get(kind, {})
    if files and not allowed:
        raise ValueError("This kind of data source takes no certificate files: use the options in its URL")
    if kind == "mongodb" and "key" in files and "cert" not in files:
        raise ValueError("A client key needs its client certificate")
    ssh = saved.get("ssh")
    if "ssh" in raw:
        ssh = None if raw["ssh"] is None else _clean_ssh(raw["ssh"] if isinstance(raw["ssh"], dict) else {}, saved.get("ssh") or {})
    if ssh and kind not in SSH_KINDS:
        raise ValueError("This kind of data source cannot go through an SSH tunnel")
    result: dict[str, Any] = {}
    if files:
        result["files"] = files
    if ssh:
        result["ssh"] = ssh
    return result


def summary(extra: dict[str, Any] | None) -> dict[str, Any]:
    """What the wizard may show about saved options: file names and the SSH server, never a secret."""
    extra = extra or {}
    result: dict[str, Any] = {"files": {role: item.get("name", role) for role, item in (extra.get("files") or {}).items()}}
    ssh = extra.get("ssh")
    if ssh:
        result["ssh"] = {"host": ssh.get("host"), "port": ssh.get("port", 22), "user": ssh.get("user"),
                         "auth": "key" if ssh.get("privateKey") else "password", "hostKey": ssh.get("hostKey", "")}
    return result


# ----------------------------------------------------------------- applying them when a connection is opened
def _with_query(url: str, values: dict[str, str]) -> str:
    """The URL with these query options set (others kept). Works for multi-host MongoDB URLs too."""
    base, _, rest = url.partition("?")
    query, hash_mark, fragment = rest.partition("#")
    if base.startswith(("mongodb://", "mongodb+srv://")) and "/" not in base.partition("://")[2]:
        base += "/"                                       # MongoDB needs a "/" between the hosts and the options
    names = {key.lower() for key in values}
    # The other options stay exactly as written: drivers read some of them (readPreferenceTags) without decoding.
    kept = [piece for piece in query.split("&") if piece and unquote(piece.partition("=")[0]).lower() not in names]
    added = [f"{quote(key, safe='')}={quote(value, safe='/:')}" for key, value in values.items()]
    return f"{base}?{'&'.join([*kept, *added])}{hash_mark}{fragment}"


def _mongo_hosts(url: str) -> tuple[str, str, list[str], str]:
    """(scheme, userinfo, hosts, rest) of a MongoDB URL; userinfo keeps its '@'."""
    scheme, _, after = url.partition("://")
    end = min([index for index in (after.find("/"), after.find("?")) if index >= 0] or [len(after)])
    netloc, rest = after[:end], after[end:]
    userinfo, at, hosts = netloc.rpartition("@")
    return scheme, userinfo + at, [item for item in hosts.split(",") if item], rest


def _split_host(text: str, default_port: int) -> tuple[str, int]:
    if text.startswith("["):
        host, _, port = text[1:].partition("]")
        port = port.lstrip(":")
    else:
        host, _, port = text.partition(":")
    return host, int(port or default_port)


def _tunnel_mongo(url: str, ssh: dict[str, Any]) -> str:
    scheme, userinfo, hosts, rest = _mongo_hosts(url)
    if scheme == "mongodb+srv":
        raise ConnectorQueryError("An SSH tunnel needs a mongodb:// URL with a host name (not mongodb+srv://): "
                                  "give one member of the replica set")
    if not hosts:
        raise ConnectorQueryError("The MongoDB URL has no host")
    host, port = _split_host(hosts[0], 27017)
    local = tunnel.local_port(ssh, host, port)
    # One server through the tunnel: the driver must not try to reach the other members by their own names.
    return _with_query(f"{scheme}://{userinfo}127.0.0.1:{local}{rest}", {"directConnection": "true"})


def _tunnel_url(kind: str, url: str, ssh: dict[str, Any]) -> str:
    from backend.app.connectors.sql import dialect  # noqa: PLC0415 - sql imports this module

    parts = urlsplit(url)
    if not parts.hostname:
        raise ConnectorQueryError("The URL has no host for the SSH tunnel")
    local = tunnel.local_port(ssh, parts.hostname, parts.port or dialect(kind).default_port)
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = f"{userinfo}@127.0.0.1:{local}" if userinfo else f"127.0.0.1:{local}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def resolve(kind: str, url: str, extra: dict[str, Any] | None) -> str:
    """The URL to open: certificate files written and added, and the host replaced by an SSH tunnel."""
    if not extra or not url:
        return url
    files = extra.get("files") or {}
    names = FILE_OPTIONS.get(kind, {})
    values: dict[str, str] = {}
    if "ca" in files and "ca" in names:
        values[names["ca"]] = _write(_file_bytes(files["ca"]))
    if kind == "mongodb" and "cert" in files:
        joined = _file_bytes(files["cert"]).rstrip() + b"\n" + (_file_bytes(files["key"]) if "key" in files else b"")
        values["tlsCertificateKeyFile"] = _write(joined)
    else:
        for role in ("cert", "key"):
            if role in files and role in names:
                values[names[role]] = _write(_file_bytes(files[role]))
    if values and kind == "mongodb":
        values.setdefault("tls", "true")
    if values:
        url = _with_query(url, values)
    ssh = extra.get("ssh")
    if ssh and kind in SSH_KINDS:
        url = _tunnel_mongo(url, ssh) if kind == "mongodb" else _tunnel_url(kind, url, ssh)
    return url


def check_ssh(extra: dict[str, Any] | None) -> str:
    """Sign in to the SSH server of these options (a test); its host key fingerprint, or "" without SSH."""
    ssh = (extra or {}).get("ssh")
    return tunnel.check_host(ssh) if ssh else ""
