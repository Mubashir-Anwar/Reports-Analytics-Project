"""Data adapter profiles (connections.json) and where each one's credentials come from."""
from __future__ import annotations

import os

from backend.app.constants import ADAPTER_TYPES, CONNECTION_ID_PATTERN, ENV_KEY_PATTERN
from backend.app.logs import LOG
from backend.app.security.secrets import _load_private_secrets
from backend.app.storage.files import normalize_folder_path
from backend.app.storage.store import get_store


def _profile_credentials(profile: dict[str, str]) -> tuple[str, str]:
    if profile.get("storage") == "file":
        saved = _load_private_secrets()["mongo_connections"].get(profile["id"], {})
        if not isinstance(saved, dict):
            return "", ""
        return str(saved.get("uri", "")).strip(), str(saved.get("database", profile.get("database", ""))).strip()
    return os.getenv(profile.get("uriEnv", ""), "").strip(), os.getenv(profile.get("databaseEnv", ""), "").strip()


DEFAULT_REMOVED = "meta/default-adapter.json"


def _env_default() -> bool:
    if not (os.getenv("MONGODB_URI", "").strip() and os.getenv("MONGODB_DATABASE", "").strip()):
        return False
    return not (get_store().read_json(DEFAULT_REMOVED) or {}).get("removed")


# The part of the product a data source belongs to: it works only there (services/adapter_areas.py).
AREAS = ("designer", "server", "analytics", "query")
AREA_NAMES = {"designer": "the Designer", "server": "the Report Server", "analytics": "Analytics", "query": "Query"}


def usable(profile: dict[str, str], area: str) -> bool:
    """May `area` use this data source? "" = one from before areas: every part may."""
    return profile.get("area", "") in ("", area)


def profiles_in(area: str) -> list[dict[str, str]]:
    return [item for item in load_connection_profiles() if usable(item, area)]


def load_connection_profiles() -> list[dict[str, str]]:
    profiles: list[dict[str, str]] = []
    raw = get_store().read_json("connections.json")
    if raw is not None:
        items = raw.get("connections", []) if isinstance(raw, dict) else []
        if not isinstance(items, list):
            raise ValueError("connections in connections.json must be an array")
        for item in items:
            if not isinstance(item, dict):
                continue
            profile = {
                "id": str(item.get("id", "")).strip(),
                "name": str(item.get("name", "")).strip(),
                # database kind: mongodb, or a SQL database run by connectors/sql.py
                "type": str(item.get("type", "mongodb")).strip().lower() if str(item.get("type", "mongodb")).strip().lower() in ADAPTER_TYPES else "mongodb",
            }
            storage = str(item.get("storage", "env")).strip().lower()
            if storage == "file":
                profile["storage"] = "file"
                profile["database"] = str(item.get("database", "")).strip()
                valid = CONNECTION_ID_PATTERN.fullmatch(profile["id"]) and profile["name"]
            else:
                profile["storage"] = "env"
                profile["uriEnv"] = str(item.get("uriEnv", "")).strip()
                profile["databaseEnv"] = str(item.get("databaseEnv", "")).strip()
                valid = (
                    CONNECTION_ID_PATTERN.fullmatch(profile["id"])
                    and profile["name"]
                    and ENV_KEY_PATTERN.fullmatch(profile["uriEnv"])
                    and ENV_KEY_PATTERN.fullmatch(profile["databaseEnv"])
                )
            if str(item.get("area") or "") in AREAS:
                profile["area"] = str(item["area"])
            folder = str(item.get("folder", "") or "").strip()
            if folder:
                try:
                    profile["folder"] = normalize_folder_path(folder)
                except ValueError:
                    LOG.warning("Data adapter %s has an invalid folder; it is shown at the top level", profile["id"])
            if valid:
                profiles.append(profile)
    # No data source is there by itself. Older installations that set MONGODB_URI / MONGODB_DATABASE keep their
    # "default" one until somebody deletes it.
    if not any(item["id"] == "default" for item in profiles) and _env_default():
        profiles.insert(0, {
            "id": "default",
            "name": "Default MongoDB",
            "storage": "env",
            "uriEnv": "MONGODB_URI",
            "databaseEnv": "MONGODB_DATABASE",
        })
    return profiles
