"""Security state that every app instance must see: signed-out sessions and failed sign-ins.

Kept in the store (files or PostgreSQL), so a logout on one instance also ends the session on the others, and the
sign-in limit counts attempts on all instances. Reads of the revoked list are cached for a couple of seconds."""
from __future__ import annotations

import threading
import time

from backend.app.storage.store import get_store

REVOKED_KEY = "meta/revoked-sessions.json"
FAILURES_KEY = "meta/login-failures.json"
CACHE_SECONDS = 2.0
_LOCK = threading.Lock()
_cache: dict[str, object] = {"at": 0.0, "revoked": {}, "store": None}


def _now() -> float:
    return time.time()


def _where(store) -> str:
    """Which store the cache belongs to (tests and tools may switch the data directory)."""
    return getattr(store, "url", None) or str(store.root)


def revoke_session(nonce: str, lifetime_seconds: float) -> None:
    if not nonce:
        return
    with _LOCK:
        store = get_store()
        revoked = {n: exp for n, exp in (store.read_json(REVOKED_KEY) or {}).items() if exp > _now()}
        revoked[nonce] = _now() + lifetime_seconds
        store.write_json(REVOKED_KEY, revoked)
        _cache.update({"at": _now(), "revoked": revoked, "store": _where(store)})


def is_revoked(nonce: str | None) -> bool:
    if not nonce:
        return False
    store = get_store()
    if _now() - float(_cache["at"]) > CACHE_SECONDS or _cache["store"] != _where(store):
        try:
            _cache.update({"at": _now(), "revoked": store.read_json(REVOKED_KEY) or {}, "store": _where(store)})
        except (OSError, ValueError):
            pass
    expiry = _cache["revoked"].get(nonce)  # type: ignore[union-attr]
    return bool(expiry and expiry > _now())


def recent_failures(client: str, window_seconds: float = 600) -> int:
    failures = (get_store().read_json(FAILURES_KEY) or {}).get(client, [])
    return sum(1 for stamp in failures if stamp > _now() - window_seconds)


def record_failure(client: str, window_seconds: float = 600) -> None:
    with _LOCK:
        store = get_store()
        data = {c: [s for s in stamps if s > _now() - window_seconds] for c, stamps in (store.read_json(FAILURES_KEY) or {}).items()}
        data = {c: stamps for c, stamps in data.items() if stamps}
        data.setdefault(client, []).append(_now())
        store.write_json(FAILURES_KEY, data)


def clear_failures(client: str) -> None:
    with _LOCK:
        store = get_store()
        data = store.read_json(FAILURES_KEY) or {}
        if client in data:
            data.pop(client)
            store.write_json(FAILURES_KEY, data)
