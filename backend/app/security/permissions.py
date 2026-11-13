"""Folder permissions for the /server repository (like JasperReports Server's Permissions dialog).

Every folder can give a user or a role one of three levels:
  none    the folder and its reports are hidden
  run     see the reports, run them, schedule them (designers)
  manage  also edit, copy, move into, upload into, restore versions (designer and admin roles only)

A rule applies to the folder and every subfolder below it. For a report in A/B/C the rules of A/B/C, then A/B, then A,
then the root ("") are tried; the first folder with a rule for the user wins, and in one folder a rule for the
user wins over a rule for the role. With no rule at all the role decides, as before: viewer = run, designer =
manage. A role caps what a rule can give (a viewer never gets manage). Administrators always have manage.

The rules live in the store under folder-permissions.json, so every instance sees the same rules."""
from __future__ import annotations

from typing import Any

from backend.app.constants import USER_ROLES
from backend.app.storage.store import get_store

KEY = "folder-permissions.json"
NONE, RUN, MANAGE = 0, 1, 2
LEVELS = {"none": NONE, "run": RUN, "manage": MANAGE}
NAMES = {value: name for name, value in LEVELS.items()}
ROLE_CAP = {"viewer": RUN, "designer": MANAGE, "admin": MANAGE}
ROLE_DEFAULT = {"viewer": RUN, "designer": MANAGE, "admin": MANAGE}


def load_rules() -> dict[str, dict[str, dict[str, str]]]:
    raw = get_store().read_json(KEY) or {}
    folders = raw.get("folders", {}) if isinstance(raw, dict) else {}
    return {str(folder): {"users": dict(entry.get("users") or {}), "roles": dict(entry.get("roles") or {})}
            for folder, entry in folders.items() if isinstance(entry, dict)}


def clean_entry(raw: Any) -> dict[str, dict[str, str]]:
    """Check the rules of one folder sent by the admin UI: {"users": {name: level}, "roles": {role: level}}."""
    if not isinstance(raw, dict):
        raise ValueError("The permissions must be a JSON object with users and roles")
    users = raw.get("users") or {}
    roles = raw.get("roles") or {}
    if not isinstance(users, dict) or not isinstance(roles, dict) or len(users) > 500:
        raise ValueError("users and roles must be objects of name: level")
    for level in [*users.values(), *roles.values()]:
        if level not in LEVELS:
            raise ValueError("A level must be none, run or manage")
    unknown = [role for role in roles if role not in USER_ROLES or role == "admin"]
    if unknown:
        raise ValueError("Roles are viewer and designer (administrators always have every permission)")
    return {"users": {str(name).casefold(): level for name, level in users.items() if str(name).strip()},
            "roles": dict(roles)}


def save_rules_for(folder: str, entry: dict[str, dict[str, str]]) -> None:
    raw = get_store().read_json(KEY) or {}
    folders = raw.get("folders", {}) if isinstance(raw, dict) else {}
    if entry["users"] or entry["roles"]:
        folders[folder] = entry
    else:
        folders.pop(folder, None)
    get_store().write_json(KEY, {"folders": folders})


def remove_rules_under(folder: str) -> None:
    """A deleted folder takes its rules (and those of its subfolders) with it."""
    raw = get_store().read_json(KEY) or {}
    folders = raw.get("folders", {}) if isinstance(raw, dict) else {}
    kept = {key: value for key, value in folders.items() if key != folder and not key.startswith(f"{folder}/")}
    if kept != folders:
        get_store().write_json(KEY, {"folders": kept})


def folder_chain(folder: str) -> list[str]:
    parts = [part for part in str(folder or "").split("/") if part]
    return ["/".join(parts[:index]) for index in range(len(parts), 0, -1)] + [""]


def explain(session: dict[str, Any], folder: str, rules: dict | None = None) -> tuple[int, str]:
    """(level, why) for a signed-in user on a folder."""
    role = session.get("role", "viewer")
    if role == "admin":
        return MANAGE, "administrator"
    cap = ROLE_CAP.get(role, NONE)
    rules = load_rules() if rules is None else rules
    username = str(session.get("username", "")).casefold()
    for candidate in folder_chain(folder):
        entry = rules.get(candidate)
        if not entry:
            continue
        where = f"/{candidate}" if candidate else "the root"
        if username in entry["users"]:
            return min(LEVELS[entry["users"][username]], cap), f"user rule on {where}"
        if role in entry["roles"]:
            return min(LEVELS[entry["roles"][role]], cap), f"{role} rule on {where}"
    return ROLE_DEFAULT.get(role, NONE), f"default for {role}"


def level(session: dict[str, Any], folder: str, rules: dict | None = None) -> int:
    return explain(session, folder, rules)[0]
