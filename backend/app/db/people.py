"""Schema v2, step 2: people and access, on every backend.

    users                one row per person (user_id, username, full_name, email, phone, password hash, …)
    user_rights          one row per app right of a user (designer, server, runner, analytics, admin)
    user_groups          teams (Finance, Branch Karachi …)
    user_group_members   who is in which team
    folder_permissions   none / run / manage on a folder id, for a user, a group or a role
    password_resets      forgot password: an e-mailed link or a request an admin approves
    api_keys             keys of other systems, with api_key_folders and api_key_networks

The rest of the service keeps the shapes it used before (a user is {"username", "rights", "superAdmin", "salt",
"hash", …}; the rules of a folder are {"users": {name: level}, "roles": {role: level}, "groups": {id: level}}; a
key is {"id", "hash", "folders": [paths], "allowedIps": [ranges], …}). This module turns them into rows and back.
upgrade_people() copies what the previous version kept in rs_users / rs_api_keys / rs_folder_permissions once.
"""
from __future__ import annotations

from typing import Any

from backend.app.db import entities as ent, model

ROOT = ""                       # the root folder: folder_id empty


def _db():
    return ent.get_entities()


def _store():
    from backend.app.storage.store import get_store  # noqa: PLC0415
    return get_store()


def _same(a: Any, b: Any) -> bool:
    return str(a or "").strip().casefold() == str(b or "").strip().casefold()


# ------------------------------------------------------------------ users
_USER_COLUMNS = {   # old record field -> users column
    "superAdmin": "isSuperAdmin", "disabled": "isDisabled", "salt": "passwordSalt", "hash": "passwordHash",
    "iterations": "passwordIterations",
}
_BACK = {column: name for name, column in _USER_COLUMNS.items()}


def _user_record(row: dict[str, Any], rights: list[str]) -> dict[str, Any]:
    record = {_BACK.get(name, name): value for name, value in row.items()}
    record["rights"] = sorted(rights)
    record["superAdmin"] = bool(row.get("isSuperAdmin"))
    record.pop("role", None)                                    # derived from the rights when it is needed
    return record


def load_users() -> list[dict[str, Any]]:
    db = _db()
    rights: dict[str, list[str]] = {}
    for row in db.find(ent.USER_RIGHTS):
        rights.setdefault(row["userId"], []).append(row["rightName"])
    users = [_user_record(row, rights.get(row["userId"], [])) for row in db.find(ent.USERS_V2)]
    return sorted(users, key=lambda item: (item.get("createdAt") or "", item["username"]))


def _user_row(record: dict[str, Any], user_id: str) -> dict[str, Any]:
    from backend.app.security.users import derived_role, is_super_admin, user_rights  # noqa: PLC0415
    rights = user_rights(record)
    row = {_USER_COLUMNS.get(name, name): value for name, value in record.items() if name not in ("rights", "role", "userId")}
    row.update({"userId": user_id, "isSuperAdmin": is_super_admin(record), "role": derived_role(rights, is_super_admin(record)),
                "email": str(record.get("email") or "").strip() or None, "phone": str(record.get("phone") or "").strip() or None,
                "fullName": str(record.get("fullName") or "").strip() or None})
    return row


def save_users(users: list[dict[str, Any]], *, by: str = "") -> None:
    """Make the users table equal to this list (matched by user id, else by username); rights follow."""
    from backend.app.security.users import user_rights  # noqa: PLC0415
    db = _db()
    current = db.find(ent.USERS_V2)
    by_id = {row["userId"]: row for row in current}
    kept: set[str] = set()
    for record in users:
        found = by_id.get(str(record.get("userId") or "")) or next((row for row in current if _same(row["username"], record["username"])), None)
        user_id = found["userId"] if found else str(record.get("userId") or model.new_id())
        record["userId"] = user_id
        kept.add(user_id)
        row = _user_row(record, user_id)
        db.put(ent.USERS_V2, row)
        wanted = set(user_rights(record))
        have = {item["rightName"] for item in db.find(ent.USER_RIGHTS, {"userId": user_id})}
        for name in have - wanted:
            db.delete(ent.USER_RIGHTS, user_id, name)
        for name in wanted - have:
            db.put(ent.USER_RIGHTS, {"userId": user_id, "rightName": name, "grantedBy": by or None, "grantedAt": model.now()})
    for user_id in set(by_id) - kept:
        _forget_user(user_id)


def _forget_user(user_id: str) -> None:
    """A deleted user takes their rights, team places, folder rules and reset requests along."""
    db = _db()
    db.delete(ent.USERS_V2, user_id)
    db.delete_where(ent.USER_RIGHTS, {"userId": user_id})
    db.delete_where(ent.USER_GROUP_MEMBERS, {"userId": user_id})
    db.delete_where(ent.FOLDER_ACCESS, {"principalType": "user", "principalId": user_id})
    db.delete_where(ent.PASSWORD_RESETS, {"userId": user_id})


def put_user(record: dict[str, Any]) -> None:
    """Save one user that already exists (e.g. last sign-in time), without touching the others."""
    found = find_user_row(record["username"])
    if found is None:
        save_users([*load_users(), record])
        return
    _db().put(ent.USERS_V2, _user_row(record, found["userId"]))


def find_user_row(username: str) -> dict[str, Any] | None:
    found = _db().find(ent.USERS_V2, {"username": username}, limit=1)
    if found:
        return found[0]
    return next((row for row in _db().find(ent.USERS_V2) if _same(row["username"], username)), None)


def taken(field: str, value: str, *, except_username: str = "") -> bool:
    """Is this username / email / phone already used by another account? (case and spaces do not count)"""
    column = {"username": "username", "email": "email", "phone": "phone"}[field]
    wanted = _clean_phone(value) if field == "phone" else str(value or "").strip().casefold()
    if not wanted:
        return False
    for row in _db().find(ent.USERS_V2):
        if except_username and _same(row["username"], except_username):
            continue
        have = _clean_phone(row.get(column)) if field == "phone" else str(row.get(column) or "").strip().casefold()
        if have and have == wanted:
            return True
    return False


def _clean_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "+")


# ------------------------------------------------------------------ teams
def load_groups() -> list[dict[str, Any]]:
    """[{groupId, name, description, members: [username]}], by name."""
    db = _db()
    names = {row["userId"]: row["username"] for row in db.find(ent.USERS_V2)}
    members: dict[str, list[str]] = {}
    for row in db.find(ent.USER_GROUP_MEMBERS):
        if row["userId"] in names:
            members.setdefault(row["groupId"], []).append(names[row["userId"]])
    return sorted(({**row, "members": sorted(members.get(row["groupId"], []), key=str.casefold)} for row in db.find(ent.USER_GROUPS)),
                  key=lambda item: item["name"].casefold())


def save_group(name: str, description: str, members: list[str], *, group_id: str = "", by: str = "") -> dict[str, Any]:
    db = _db()
    for other in db.find(ent.USER_GROUPS):
        if _same(other["name"], name) and other["groupId"] != group_id:
            raise ValueError(f"A team called {other['name']} already exists")
    existing = db.get(ent.USER_GROUPS, group_id) if group_id else None
    if group_id and existing is None:
        raise LookupError("This team does not exist")
    row = {**(existing or {}), "groupId": group_id or model.new_id(), "name": name, "description": description or None,
           "createdBy": (existing or {}).get("createdBy") or by or None, "createdAt": (existing or {}).get("createdAt") or model.now()}
    db.put(ent.USER_GROUPS, row)
    ids = {}
    for username in members:
        found = find_user_row(username)
        if found is None:
            raise ValueError(f"There is no user {username}")
        ids[found["userId"]] = found
    have = {item["userId"] for item in db.find(ent.USER_GROUP_MEMBERS, {"groupId": row["groupId"]})}
    for user_id in have - set(ids):
        db.delete(ent.USER_GROUP_MEMBERS, row["groupId"], user_id)
    for user_id in set(ids) - have:
        db.put(ent.USER_GROUP_MEMBERS, {"groupId": row["groupId"], "userId": user_id, "addedBy": by or None, "addedAt": model.now()})
    return next(item for item in load_groups() if item["groupId"] == row["groupId"])


def delete_group(group_id: str) -> bool:
    db = _db()
    if not db.delete(ent.USER_GROUPS, group_id):
        return False
    db.delete_where(ent.USER_GROUP_MEMBERS, {"groupId": group_id})
    db.delete_where(ent.FOLDER_ACCESS, {"principalType": "group", "principalId": group_id})
    return True


def groups_of(username: str) -> list[str]:
    found = find_user_row(username)
    return [row["groupId"] for row in _db().find(ent.USER_GROUP_MEMBERS, {"userId": found["userId"]})] if found else []


# ------------------------------------------------------------------ folder permissions
def _folder_id(path: str, create: bool) -> str | None:
    return _store().folder_id("reports", path, create=create) if path else None


def load_rules() -> dict[str, dict[str, dict[str, str]]]:
    db = _db()
    paths = _store().folder_paths("reports")
    names = {row["userId"]: str(row["username"]).casefold() for row in db.find(ent.USERS_V2)}
    rules: dict[str, dict[str, dict[str, str]]] = {}
    for row in db.find(ent.FOLDER_ACCESS):
        folder = paths.get(row["folderId"]) if row.get("folderId") else ROOT
        if folder is None:
            continue                                            # its folder is gone
        entry = rules.setdefault(folder, {"users": {}, "roles": {}, "groups": {}})
        if row["principalType"] == "user" and row["principalId"] in names:
            entry["users"][names[row["principalId"]]] = row["accessLevel"]
        elif row["principalType"] == "group":
            entry["groups"][row["principalId"]] = row["accessLevel"]
        elif row["principalType"] == "role":
            entry["roles"][row["principalId"]] = row["accessLevel"]
    return rules


def save_rules_for(folder: str, entry: dict[str, dict[str, str]], *, by: str = "") -> None:
    """Make the rules of one folder equal to entry {"users": {name: level}, "roles": {…}, "groups": {id: level}}."""
    db = _db()
    folder_id = _folder_id(folder, create=bool(entry.get("users") or entry.get("roles") or entry.get("groups")))
    if folder and folder_id is None:
        return                                                  # no folder and nothing to keep
    wanted: dict[tuple[str, str], str] = {}
    for username, level in (entry.get("users") or {}).items():
        found = find_user_row(username)
        if found is not None:
            wanted[("user", found["userId"])] = level
    for group_id, level in (entry.get("groups") or {}).items():
        wanted[("group", group_id)] = level
    for role, level in (entry.get("roles") or {}).items():
        wanted[("role", role)] = level
    current = [row for row in db.find(ent.FOLDER_ACCESS) if (row.get("folderId") or None) == folder_id]
    for row in current:
        place = (row["principalType"], row["principalId"])
        if place not in wanted:
            db.delete(ent.FOLDER_ACCESS, row["permissionId"])
        elif wanted[place] != row["accessLevel"]:
            db.put(ent.FOLDER_ACCESS, {**row, "accessLevel": wanted[place], "grantedBy": by or None, "grantedAt": model.now()})
    have = {(row["principalType"], row["principalId"]) for row in current}
    for (kind, principal), level in wanted.items():
        if (kind, principal) not in have:
            db.put(ent.FOLDER_ACCESS, {"permissionId": model.new_id(), "folderId": folder_id, "principalType": kind,
                                       "principalId": principal, "accessLevel": level, "grantedBy": by or None, "grantedAt": model.now()})


def remove_rules_under(folder: str) -> None:
    paths = _store().folder_paths("reports")
    db = _db()
    for row in db.find(ent.FOLDER_ACCESS):
        path = paths.get(row.get("folderId") or "")
        if path is None and row.get("folderId"):
            db.delete(ent.FOLDER_ACCESS, row["permissionId"])     # its folder is gone already
        elif path is not None and (path == folder or path.startswith(f"{folder}/")):
            db.delete(ent.FOLDER_ACCESS, row["permissionId"])


# ------------------------------------------------------------------ API keys
def _key_record(row: dict[str, Any], folders: list[str], networks: list[str]) -> dict[str, Any]:
    record = {name: value for name, value in row.items() if name not in ("apiKeyId", "keyHash", "isDisabled")}
    record.update({"id": row["apiKeyId"], "hash": row["keyHash"], "disabled": bool(row.get("isDisabled")),
                   "folders": sorted(folders), "allowedIps": networks})
    return record


def load_keys() -> list[dict[str, Any]]:
    db = _db()
    paths = _store().folder_paths("reports")
    folders: dict[str, list[str]] = {}
    for row in db.find(ent.API_KEY_FOLDERS):
        if row["folderId"] in paths:
            folders.setdefault(row["apiKeyId"], []).append(paths[row["folderId"]])
    networks: dict[str, list[str]] = {}
    for row in db.find(ent.API_KEY_NETWORKS):
        networks.setdefault(row["apiKeyId"], []).append(row["ipRange"])
    return [_key_record(row, folders.get(row["apiKeyId"], []), networks.get(row["apiKeyId"], [])) for row in db.find(ent.API_KEYS_V2)]


def put_key(record: dict[str, Any]) -> None:
    db = _db()
    key_id = record["id"]
    row = {name: value for name, value in record.items() if name not in ("id", "hash", "disabled", "folders", "allowedIps")}
    db.put(ent.API_KEYS_V2, {**row, "apiKeyId": key_id, "keyHash": record["hash"], "isDisabled": bool(record.get("disabled"))})
    wanted_folders = {_folder_id(str(path).strip("/"), create=True) for path in record.get("folders") or [] if str(path).strip("/")}
    have = {row["folderId"] for row in db.find(ent.API_KEY_FOLDERS, {"apiKeyId": key_id})}
    for folder_id in have - wanted_folders:
        db.delete(ent.API_KEY_FOLDERS, key_id, folder_id)
    for folder_id in wanted_folders - have:
        db.put(ent.API_KEY_FOLDERS, {"apiKeyId": key_id, "folderId": folder_id})
    wanted_networks = {str(item).strip() for item in record.get("allowedIps") or [] if str(item).strip()}
    have = {row["ipRange"] for row in db.find(ent.API_KEY_NETWORKS, {"apiKeyId": key_id})}
    for value in have - wanted_networks:
        db.delete(ent.API_KEY_NETWORKS, key_id, value)
    for value in wanted_networks - have:
        db.put(ent.API_KEY_NETWORKS, {"apiKeyId": key_id, "ipRange": value})


def touch_key(key_id: str, when: str) -> None:
    row = _db().get(ent.API_KEYS_V2, key_id)
    if row is not None:
        _db().put(ent.API_KEYS_V2, {**row, "lastUsedAt": when})


def save_keys(keys: list[dict[str, Any]]) -> None:
    db = _db()
    wanted = {item["id"] for item in keys}
    for row in db.find(ent.API_KEYS_V2):
        if row["apiKeyId"] not in wanted:
            db.delete(ent.API_KEYS_V2, row["apiKeyId"])
            db.delete_where(ent.API_KEY_FOLDERS, {"apiKeyId": row["apiKeyId"]})
            db.delete_where(ent.API_KEY_NETWORKS, {"apiKeyId": row["apiKeyId"]})
    for item in keys:
        put_key(item)


# ------------------------------------------------------------------ one-time copy from the previous version
def upgrade_people(store: Any) -> dict[str, int]:
    """rs_users, rs_api_keys and rs_folder_permissions -> the step 2 tables, once (the old tables are kept)."""
    from backend.app.security.users import is_super_admin, user_rights  # noqa: PLC0415
    from backend.app.storage.routed import UPGRADE_KEY  # noqa: PLC0415
    plain = store                                   # meta/schema-v2.json is a plain document
    state = plain.read_json(UPGRADE_KEY, None) or {}
    counts = {"users": 0, "rights": 0, "apiKeys": 0, "folderRules": 0}
    if state.get("step2"):
        return counts
    db = _db()
    old_users = db.find(ent.USERS)
    if old_users and not db.find(ent.USERS_V2):
        records = []
        for user in old_users:
            record = dict(user)
            record["rights"] = sorted(user_rights(user))             # older accounts: rights from the role
            record["superAdmin"] = is_super_admin(user)
            records.append(record)
        save_users(records, by="upgrade")
        counts["users"] = len(records)
        counts["rights"] = len(db.find(ent.USER_RIGHTS))
    old_keys = db.find(ent.API_KEYS)
    if old_keys and not db.find(ent.API_KEYS_V2):
        for key in old_keys:
            put_key(key)
        counts["apiKeys"] = len(old_keys)
    old_rules = db.find(ent.FOLDER_PERMISSIONS)
    if old_rules and not db.find(ent.FOLDER_ACCESS):
        for row in old_rules:
            folder = "" if row["folder"] == "/" else row["folder"]
            save_rules_for(folder, {"users": row.get("users") or {}, "roles": row.get("roles") or {}}, by="upgrade")
            counts["folderRules"] += len(row.get("users") or {}) + len(row.get("roles") or {})
    plain.write_json(UPGRADE_KEY, {**(plain.read_json(UPGRADE_KEY, None) or {}), "step2": model.iso(model.now()), "people": counts})
    if any(counts.values()):
        from backend.app.logs import LOG  # noqa: PLC0415
        LOG.info("Schema v2 (people and access): %s", counts)
    return counts
