"""Where a scheduled report's file goes after it ran, besides /server → Runs and e-mail.

A schedule has up to MAX_TARGETS targets in job["deliver"]:

    {"id": "t1", "type": "sftp",   "label": "Bank SFTP", "host": "sftp.bank.com", "port": 22, "username": "reports",
     "folder": "/incoming/daily", "fileName": "{reportId}_{date}.{format}", "hostKey": "SHA256:…"}
    {"id": "t2", "type": "http",   "label": "ERP", "url": "https://erp.example.com/api/reports", "method": "POST",
     "body": "multipart" | "raw", "fileName": "…"}
    {"id": "t3", "type": "folder", "label": "Share", "folder": "\\\\fileserver\\reports\\daily", "fileName": "…"}

Passwords, private keys and HTTP header values are never kept in the schedule: they are in the private secrets
(encrypted with REPORTING_SECRETS_KEY), under delivery_secrets["<job id>/<target id>"].

Safety
- SFTP: the server's host key is remembered on the first connection ("trust on first use") and must match after that;
  give hostKey ("SHA256:…") to pin it from the start.
- HTTP: only http/https, no internal addresses (unless REPORTING_WEBHOOK_ALLOW_PRIVATE=true), no redirects, 60 s.
- Folder: only below a folder the administrator allowed in REPORTING_DELIVERY_FOLDERS (separated by ;). Without it,
  folder delivery is off. Only administrators may choose it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import IO, Any
from urllib.parse import urlparse

from backend.app.logs import LOG

TYPES = ("sftp", "http", "folder")
MAX_TARGETS = 5
HTTP_TIMEOUT = 60
SFTP_TIMEOUT = 20
DEFAULT_NAME = "{reportId}_{date}.{format}"
NAME_TOKENS = ("reportId", "title", "date", "time", "runId", "format", "label")
_HEADER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}")
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")


# ----------------------------------------------------------------- file names
def file_name(template: str, run: dict[str, Any], label: str = "") -> str:
    """The file name from a template such as "{reportId}_{date}.{format}" (unknown {tokens} stay as written)."""
    finished = run.get("finishedAt") or datetime.now(timezone.utc).isoformat()
    try:
        moment = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
    except ValueError:
        moment = datetime.now(timezone.utc)
    values = {"reportId": run.get("reportId", "report"), "title": run.get("title") or run.get("reportId", "report"),
              "date": moment.strftime("%Y-%m-%d"), "time": moment.strftime("%H%M%S"), "runId": run.get("id", ""),
              "format": run.get("format", "pdf"), "label": label}
    name = re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template or DEFAULT_NAME)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .") or f"report.{values['format']}"
    if not name.lower().endswith(f".{values['format']}"):
        name = f"{name}.{values['format']}"
    return name[:180]


# ----------------------------------------------------------------- checking a target sent by the UI
def allowed_roots() -> list[Path]:
    raw = os.getenv("REPORTING_DELIVERY_FOLDERS", "")
    return [Path(part.strip()) for part in raw.split(";") if part.strip()]


def _inside_allowed(folder: str) -> Path:
    roots = allowed_roots()
    if not roots:
        raise ValueError("Delivery to a folder is off. The administrator allows folders with REPORTING_DELIVERY_FOLDERS "
                         "(for example \\\\fileserver\\reports;D:\\exports)")
    target = Path(folder)
    if not target.is_absolute():
        raise ValueError("Give the full folder path, e.g. \\\\fileserver\\reports\\daily or D:\\exports\\daily")
    resolved = Path(os.path.normpath(str(target)))
    for root in roots:
        base = Path(os.path.normpath(str(root)))
        try:
            resolved.relative_to(base)
            return resolved
        except ValueError:
            continue
    raise ValueError(f"The folder must be inside one of: {'; '.join(str(r) for r in roots)}")


def clean_targets(raw: Any, previous: list[dict[str, Any]] | None = None, *, is_admin: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Check the targets of a schedule. Returns (targets without secrets, {target id: new secrets}). A secret left
    empty keeps the saved one (the UI never receives it back)."""
    if raw in (None, ""):
        return [], {}
    if not isinstance(raw, list):
        raise ValueError("deliver must be a list of targets")
    if len(raw) > MAX_TARGETS:
        raise ValueError(f"At most {MAX_TARGETS} delivery targets per schedule")
    before = {item.get("id"): item for item in previous or []}
    targets: list[dict[str, Any]] = []
    new_secrets: dict[str, dict[str, str]] = {}
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError("Each delivery target must be a JSON object")
        kind = str(item.get("type", "")).lower()
        if kind not in TYPES:
            raise ValueError("A delivery target type is sftp, http or folder")
        target_id = str(item.get("id") or "")
        if not re.fullmatch(r"[a-z0-9]{1,16}", target_id):
            target_id = secrets.token_hex(4)
        where = f"Delivery {index}"
        label = str(item.get("label") or "").strip()[:80]
        template = str(item.get("fileName") or DEFAULT_NAME).strip()[:160]
        unknown = [t for t in re.findall(r"\{(\w+)\}", template) if t not in NAME_TOKENS]
        if unknown:
            raise ValueError(f"{where}: the file name may use {', '.join('{' + t + '}' for t in NAME_TOKENS)} (not {{{unknown[0]}}})")
        clean: dict[str, Any] = {"id": target_id, "type": kind, "label": label, "fileName": template,
                                 "enabled": item.get("enabled", True) is not False, "onlyOnSuccess": item.get("onlyOnSuccess", True) is not False}
        secret: dict[str, str] = {}
        if kind == "sftp":
            host = str(item.get("host") or "").strip()
            if not _HOST.fullmatch(host):
                raise ValueError(f"{where}: give the SFTP server name, e.g. sftp.bank.com")
            port = item.get("port") or 22
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise ValueError(f"{where}: the port is a number from 1 to 65535 (usually 22)")
            username = str(item.get("username") or "").strip()
            if not re.fullmatch(r"[^\s:/\\]{1,64}", username):
                raise ValueError(f"{where}: give the SFTP user name")
            folder = str(item.get("folder") or "/").strip().replace("\\", "/") or "/"
            if ".." in PurePosixPath(folder).parts or len(folder) > 300:
                raise ValueError(f"{where}: the remote folder may not contain ..")
            host_key = str(item.get("hostKey") or "").strip()
            if host_key and not re.fullmatch(r"SHA256:[A-Za-z0-9+/=]{20,60}", host_key):
                raise ValueError(f"{where}: the host key looks like SHA256:… (from ssh-keyscan / your SFTP provider)")
            if (item.get("hostKeyChanged") is True) or host != before.get(target_id, {}).get("host"):
                remembered = host_key                     # another server: forget the key seen before
            else:
                remembered = host_key or before.get(target_id, {}).get("hostKey", "")
            clean.update({"host": host, "port": port, "username": username, "folder": folder, "hostKey": remembered,
                          "auth": "key" if item.get("auth") == "key" else "password"})
            if item.get("password"):
                secret["password"] = str(item["password"])[:500]
            if item.get("privateKey"):
                key_text = str(item["privateKey"]).strip()
                if "PRIVATE KEY" not in key_text or len(key_text) > 20000:
                    raise ValueError(f"{where}: paste the whole private key (-----BEGIN … PRIVATE KEY----- …)")
                secret["privateKey"] = key_text
            if item.get("passphrase"):
                secret["passphrase"] = str(item["passphrase"])[:500]
        elif kind == "http":
            from backend.app.security import webhooks  # noqa: PLC0415
            url = str(item.get("url") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname:
                raise ValueError(f"{where}: the URL starts with https:// (or http://)")
            if parsed.username or parsed.password:
                raise ValueError(f"{where}: put no user name or password in the URL; use a header such as Authorization")
            webhooks.check_url(url)
            method = str(item.get("method") or "POST").upper()
            if method not in ("POST", "PUT"):
                raise ValueError(f"{where}: the method is POST or PUT")
            body = "raw" if item.get("body") == "raw" else "multipart"
            field = str(item.get("field") or "file").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", field):
                raise ValueError(f"{where}: the form field name may use letters, numbers, _ . -")
            names = []
            headers = item.get("headers") or []
            if not isinstance(headers, list) or len(headers) > 10:
                raise ValueError(f"{where}: at most 10 headers")
            kept = {h.get("name"): h for h in before.get(target_id, {}).get("headers", [])}
            for header in headers:
                name = str((header or {}).get("name") or "").strip()
                if not name:
                    continue
                if not _HEADER_NAME.fullmatch(name) or name.lower() in ("host", "content-length", "content-type", "transfer-encoding"):
                    raise ValueError(f"{where}: the header name {name[:40]!r} cannot be used")
                value = str(header.get("value") or "")
                if "\n" in value or "\r" in value:
                    raise ValueError(f"{where}: a header value is one line")
                if value:
                    secret[f"header:{name}"] = value[:2000]
                elif name not in kept:
                    raise ValueError(f"{where}: give a value for the header {name}")
                names.append({"name": name})
            clean.update({"url": url, "method": method, "body": body, "field": field, "headers": names})
        else:
            if not is_admin:
                raise ValueError(f"{where}: only administrators may deliver to a folder on the server's network")
            folder = str(item.get("folder") or "").strip()
            clean["folder"] = str(_inside_allowed(folder))
        targets.append(clean)
        if secret:
            new_secrets[target_id] = secret
    return targets, new_secrets


def public_view(targets: list[dict[str, Any]], job_id: str) -> list[dict[str, Any]]:
    """The targets for the UI: secrets replaced by "is saved" flags."""
    saved = _secrets().get(job_id, {}) if job_id else {}
    out = []
    for target in targets or []:
        stored = saved.get(target["id"], {})
        item = {**target, "hasPassword": bool(stored.get("password")), "hasPrivateKey": bool(stored.get("privateKey"))}
        if target.get("type") == "http":
            item["headers"] = [{**h, "saved": bool(stored.get(f"header:{h['name']}"))} for h in target.get("headers", [])]
        out.append(item)
    return out


# ----------------------------------------------------------------- secrets (private, encrypted when a key is set)
def _secrets() -> dict[str, dict[str, dict[str, str]]]:
    from backend.app.security.secrets import _load_private_secrets  # noqa: PLC0415
    raw = _load_private_secrets().get("delivery_secrets") or {}
    return raw if isinstance(raw, dict) else {}


def save_secrets(job_id: str, targets: list[dict[str, Any]], changed: dict[str, dict[str, str]]) -> None:
    """Keep the secrets of the job's targets: the new ones, the saved ones still used; drop the rest."""
    from backend.app.security.secrets import _load_private_secrets, _save_private_secrets  # noqa: PLC0415
    private = _load_private_secrets()
    every = dict(private.get("delivery_secrets") or {})
    old = every.get(job_id, {})
    keep: dict[str, dict[str, str]] = {}
    for target in targets:
        merged = {**old.get(target["id"], {}), **changed.get(target["id"], {})}
        if target["type"] == "http":
            wanted = {f"header:{h['name']}" for h in target.get("headers", [])}
            merged = {k: v for k, v in merged.items() if k in wanted}
        elif target["type"] == "sftp":
            merged = {k: v for k, v in merged.items() if k in ("password", "privateKey", "passphrase")}
        else:
            merged = {}
        if merged:
            keep[target["id"]] = merged
    if keep:
        every[job_id] = keep
    else:
        every.pop(job_id, None)
    if every != (private.get("delivery_secrets") or {}):
        private["delivery_secrets"] = every
        _save_private_secrets(private)


def forget_secrets(job_id: str) -> None:
    save_secrets(job_id, [], {})


def remember_host_key(job_id: str, target_id: str, fingerprint: str) -> None:
    from backend.app.jobs import store  # noqa: PLC0415
    with store.LOCK:
        schedules = store.load_schedules()
        for job in schedules:
            if job.get("id") == job_id:
                for target in job.get("deliver") or []:
                    if target.get("id") == target_id and not target.get("hostKey"):
                        target["hostKey"] = fingerprint
        store.save_schedules(schedules)


# ----------------------------------------------------------------- sending
@contextmanager
def _opened(source: bytes | Path) -> Iterator[IO[bytes]]:
    if isinstance(source, Path):
        with source.open("rb") as stream:
            yield stream
    else:
        import io  # noqa: PLC0415
        yield io.BytesIO(source)


def _size(source: bytes | Path) -> int:
    return source.stat().st_size if isinstance(source, Path) else len(source)


def fingerprint(key: Any) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")


def _sftp_client(target: dict[str, Any], secret: dict[str, str]) -> tuple[Any, Any, str]:
    """(transport, sftp, the server's key fingerprint). Raises ValueError with a plain reason."""
    try:
        import paramiko  # noqa: PLC0415
    except ImportError:
        raise ValueError("SFTP needs the paramiko package (pip install -r requirements.txt)") from None
    import socket  # noqa: PLC0415
    try:
        sock = socket.create_connection((target["host"].strip("[]"), int(target.get("port") or 22)), timeout=SFTP_TIMEOUT)
    except OSError as exc:
        raise ValueError(f"Cannot reach {target['host']}:{target.get('port', 22)} ({exc.__class__.__name__})") from None
    transport = paramiko.Transport(sock)
    transport.banner_timeout = SFTP_TIMEOUT
    transport.auth_timeout = SFTP_TIMEOUT
    try:
        transport.start_client(timeout=SFTP_TIMEOUT)
        seen = fingerprint(transport.get_remote_server_key())
        expected = target.get("hostKey") or ""
        if expected and expected.rstrip("=") != seen:
            raise ValueError(f"The SFTP server's key changed (expected {expected}, got {seen}). If the provider changed it, "
                             "edit the schedule and accept the new key")
        if target.get("auth") == "key" or secret.get("privateKey"):
            if not secret.get("privateKey"):
                raise ValueError("No private key is saved for this SFTP target")
            import io  # noqa: PLC0415
            key = None
            for kind in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                try:
                    key = kind.from_private_key(io.StringIO(secret["privateKey"]), password=secret.get("passphrase") or None)
                    break
                except paramiko.SSHException:
                    continue
            if key is None:
                raise ValueError("The private key could not be read (wrong passphrase, or not an RSA / ECDSA / Ed25519 key)")
            transport.auth_publickey(target["username"], key)
        else:
            if not secret.get("password"):
                raise ValueError("No password is saved for this SFTP target")
            transport.auth_password(target["username"], secret["password"])
        return transport, paramiko.SFTPClient.from_transport(transport), seen
    except paramiko.AuthenticationException:
        transport.close()
        raise ValueError("The SFTP server refused the user name / password (or key)") from None
    except paramiko.SSHException as exc:
        transport.close()
        raise ValueError(f"SFTP: {str(exc)[:200]}") from None
    except Exception:
        transport.close()
        raise


def _send_sftp(target: dict[str, Any], secret: dict[str, str], name: str, source: bytes | Path) -> tuple[str, str]:
    transport, sftp, seen = _sftp_client(target, secret)
    try:
        folder = target.get("folder") or "/"
        remote = str(PurePosixPath(folder) / name)
        partial = f"{remote}.part"
        with _opened(source) as stream:
            sftp.putfo(stream, partial, file_size=_size(source), confirm=True)
        try:
            sftp.posix_rename(partial, remote)              # replaces a file of the same name
        except OSError:
            try:
                sftp.remove(remote)
            except OSError:
                pass
            sftp.rename(partial, remote)
        return f"uploaded to {target['host']}:{remote}", seen
    except OSError as exc:
        raise ValueError(f"SFTP upload to {target.get('folder')} failed: {exc}") from None
    finally:
        sftp.close()
        transport.close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _send_http(target: dict[str, Any], secret: dict[str, str], name: str, source: bytes | Path, run: dict[str, Any]) -> str:
    from backend.app.security import webhooks  # noqa: PLC0415
    url = webhooks.check_url(target["url"])
    media = {"pdf": "application/pdf", "csv": "text/csv",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(run.get("format", ""), "application/octet-stream")
    if _size(source) > 100 * 1_048_576:
        raise ValueError("The file is larger than 100 MB; send big files by SFTP or to a folder")
    data = source.read_bytes() if isinstance(source, Path) else source
    info = {"reportId": run.get("reportId"), "title": run.get("title"), "runId": run.get("id"), "rowCount": run.get("rowCount", 0),
            "format": run.get("format"), "fileName": name, "finishedAt": run.get("finishedAt")}
    headers = {"User-Agent": "mongo-reporting-delivery/1", "X-Reporting-Report": str(run.get("reportId", "")),
               "X-Reporting-Run": str(run.get("id", "")), "X-Reporting-File-Name": name}
    if target.get("body") == "raw":
        body = data
        headers["Content-Type"] = media
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    else:
        boundary = "----reporting" + secrets.token_hex(12)
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="run"\r\nContent-Type: application/json\r\n\r\n'.encode()
                 + json.dumps(info, ensure_ascii=False).encode("utf-8") + b"\r\n",
                 f'--{boundary}\r\nContent-Disposition: form-data; name="{target.get("field") or "file"}"; filename="{name}"\r\n'
                 f"Content-Type: {media}\r\n\r\n".encode() + data + b"\r\n", f"--{boundary}--\r\n".encode()]
        body = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    for header in target.get("headers", []):
        value = secret.get(f"header:{header['name']}")
        if value:
            headers[header["name"]] = value
    request = urllib.request.Request(url, data=body, method=target.get("method") or "POST", headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=HTTP_TIMEOUT) as answer:  # noqa: S310 - checked
            return f"{target.get('method', 'POST')} {urlparse(url).hostname}: HTTP {answer.status}"
    except urllib.error.HTTPError as exc:
        raise ValueError(f"{urlparse(url).hostname} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f"Cannot reach {urlparse(url).hostname} ({getattr(exc, 'reason', exc)})") from None


def _send_folder(target: dict[str, Any], name: str, source: bytes | Path) -> str:
    folder = _inside_allowed(target["folder"])
    try:
        folder.mkdir(parents=True, exist_ok=True)
        partial = folder / f"{name}.part"
        if isinstance(source, Path):
            shutil.copyfile(source, partial)
        else:
            partial.write_bytes(source)
        os.replace(partial, folder / name)
    except OSError as exc:
        raise ValueError(f"Cannot write to {folder}: {exc.strerror or exc}") from None
    return f"copied to {folder / name}"


def deliver_run(job: dict[str, Any], run: dict[str, Any], source: bytes | Path | None) -> list[dict[str, Any]]:
    """Send the run's file to every target of the job. Never raises: each result is on the run."""
    results = []
    stored = _secrets().get(job.get("id", ""), {})
    for target in job.get("deliver") or []:
        if not target.get("enabled", True):
            continue
        label = target.get("label") or target.get("host") or target.get("url") or target.get("folder") or target["type"]
        if run.get("status") != "success" or source is None:
            if target.get("onlyOnSuccess", True) or source is None:
                results.append({"id": target["id"], "type": target["type"], "label": label, "ok": False, "skipped": True,
                                "result": "not sent: the report did not run"})
                continue
        name = file_name(target.get("fileName") or DEFAULT_NAME, run, target.get("label", ""))
        try:
            if target["type"] == "sftp":
                text, seen = _send_sftp(target, stored.get(target["id"], {}), name, source)
                if not target.get("hostKey"):
                    remember_host_key(job["id"], target["id"], seen)
            elif target["type"] == "http":
                text = _send_http(target, stored.get(target["id"], {}), name, source, run)
            else:
                text = _send_folder(target, name, source)
            results.append({"id": target["id"], "type": target["type"], "label": label, "ok": True, "result": text, "file": name})
        except ValueError as exc:
            results.append({"id": target["id"], "type": target["type"], "label": label, "ok": False, "result": str(exc)[:300]})
        except Exception as exc:  # noqa: BLE001 - one target failing must not stop the others
            LOG.warning("Delivery %s of job %s failed: %s", target["type"], job.get("id"), exc)
            results.append({"id": target["id"], "type": target["type"], "label": label, "ok": False,
                            "result": f"{exc.__class__.__name__}: {str(exc)[:200]}"})
    return results


def test_target(target: dict[str, Any], secret: dict[str, str]) -> dict[str, Any]:
    """Check a target without sending a report: SFTP logs in and opens the folder, HTTP checks the URL, a folder
    is checked for writing. {"ok", "result", "hostKey"?}"""
    try:
        if target["type"] == "sftp":
            transport, sftp, seen = _sftp_client(target, secret)
            try:
                sftp.listdir(target.get("folder") or "/")
            except OSError as exc:
                return {"ok": False, "result": f"Logged in, but the folder {target.get('folder')} cannot be opened: {exc}", "hostKey": seen}
            finally:
                sftp.close()
                transport.close()
            return {"ok": True, "result": f"Logged in to {target['host']} and opened {target.get('folder') or '/'}", "hostKey": seen}
        if target["type"] == "http":
            from backend.app.security import webhooks  # noqa: PLC0415
            webhooks.check_url(target["url"])
            return {"ok": True, "result": f"The address {urlparse(target['url']).hostname} may be called. (Nothing was sent: "
                                          "use “Run now” to send a real file.)"}
        folder = _inside_allowed(target["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".write-test-{secrets.token_hex(4)}"
        probe.write_bytes(b"ok")
        probe.unlink()
        return {"ok": True, "result": f"The server can write to {folder}"}
    except ValueError as exc:
        return {"ok": False, "result": str(exc)}
    except OSError as exc:
        return {"ok": False, "result": f"{exc.strerror or exc}"}
