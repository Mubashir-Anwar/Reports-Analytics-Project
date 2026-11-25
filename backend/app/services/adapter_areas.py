"""A data source belongs to one part of the product: Designer, Report Server, Analytics or Query, and works only there.

`area` of a profile is one of AREAS. "" = a data source from before this rule (or made over the API without an
area): it works in every part until an administrator gives it one part, or splits it into one per part.

What crosses a part (publishing a report, a query taken to the Designer) switches to a data source of the part it
arrives in: the one chosen, the one remembered from last time, or one with exactly the same connection."""
from __future__ import annotations

import copy
import re
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, Request

from backend.app.connectors.profiles import AREA_NAMES, AREAS, _profile_credentials, load_connection_profiles, profiles_in, usable
from backend.app.constants import CONNECTION_ID_PATTERN
from backend.app.security.secrets import _load_private_secrets, _save_private_secrets
from backend.app.storage.store import get_store

__all__ = ["managed_areas", "may_use_area", "require_area", "counterpart", "remember_counterpart", "copy_adapter", "split_adapter",
           "switch_connections", "draft_connections", "adapter_uses", "free_adapter_id", "report_connection_ids"]

LINKS = "meta/adapter-links.json"          # {"links": {"<area>": {"<id elsewhere>": "<id in area>"}}}


def find_profile(connection_id: str) -> dict[str, Any] | None:
    return next((item for item in load_connection_profiles() if item["id"] == connection_id), None)


# ------------------------------------------------------------------ rights
def managed_areas(ctx: SimpleNamespace, request: Request) -> set[str]:
    """The parts whose data sources this caller may see and change (call after require_admin(request, "sources"))."""
    session = getattr(request.state, "session", None)
    if session is None:                                         # a token: the admin token everything
        return set(AREAS) if getattr(request.state, "actor", "") != "designer token" else {"designer", "query"}
    rights = set(session.get("rights") or [])
    if session.get("superAdmin") or "admin" in rights:
        return set(AREAS)
    areas = rights & {"designer", "server", "query"}
    if {"analytics", "designer"} <= rights:                     # datasets need the Designer right too
        areas.add("analytics")
    return areas


def may_use_area(ctx: SimpleNamespace, request: Request, area: str) -> None:
    if area and area not in ctx.managed_areas(request):
        raise HTTPException(status_code=403, detail=f"Data sources of {AREA_NAMES[area]} need the right for {AREA_NAMES[area]}")


def require_area(ctx: SimpleNamespace, connection_id: str, area: str) -> None:
    """422 when this data source belongs to another part (an unknown id is reported by whoever uses it)."""
    profile = find_profile(connection_id)
    if profile is not None and not usable(profile, area):
        raise HTTPException(status_code=422, detail=f"The data source “{profile['name']}” belongs to {AREA_NAMES[profile['area']]}; "
                                                    f"choose a data source of {AREA_NAMES[area]}")


# ------------------------------------------------------------------ the same database in another part
def _links() -> dict[str, dict[str, str]]:
    found = (get_store().read_json(LINKS) or {}).get("links")
    return found if isinstance(found, dict) else {}


def remember_counterpart(ctx: SimpleNamespace, connection_id: str, area: str, other_id: str) -> None:
    links = _links()
    if connection_id != other_id and links.get(area, {}).get(connection_id) != other_id:
        links.setdefault(area, {})[connection_id] = other_id
        get_store().write_json(LINKS, {"links": links})


def counterpart(ctx: SimpleNamespace, connection_id: str, area: str) -> str:
    """The data source to use in `area` in place of this one; "" when somebody has to choose."""
    source = find_profile(connection_id)
    if source is None:
        return ""
    if usable(source, area):
        return connection_id
    kind = source.get("type", "mongodb")
    there = {item["id"]: item for item in profiles_in(area) if item.get("type", "mongodb") == kind}
    links = _links()
    linked = links.get(area, {}).get(connection_id)
    if linked in there:
        return linked
    for mine, theirs in links.get(source.get("area") or "", {}).items():      # remembered the other way round
        if theirs == connection_id and mine in there:
            return mine
    credentials = _profile_credentials(source)
    same = [key for key, item in there.items() if credentials[0] and _profile_credentials(item) == credentials]
    return same[0] if same else ""


def free_adapter_id(ctx: SimpleNamespace, wanted: str) -> str:
    taken = {item["id"] for item in load_connection_profiles()}
    base, number = wanted[:40], 1
    found = base
    while found in taken:
        number += 1
        found = f"{base[:36]}-{number}"
    return found


def _add_copy(profiles: list[dict[str, Any]], private: dict[str, Any], source: dict[str, Any], area: str, new_id: str, name: str) -> None:
    twin = {**source, "id": new_id, "name": name, "area": area}
    if area != "server":
        twin.pop("folder", None)                                # folders of the server's repository
    saved = private["mongo_connections"].get(source["id"])
    if source.get("storage") == "file" and saved is not None:
        private["mongo_connections"][new_id] = copy.deepcopy(saved)
    profiles.append(twin)


def copy_adapter(ctx: SimpleNamespace, connection_id: str, area: str, name: str = "") -> str:
    """A separate data source in `area` with the same connection (from then on the two have nothing in common)."""
    source = find_profile(connection_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Data source not found: {connection_id}")
    base = re.sub(r"-(designer|server|analytics|query)$", "", connection_id)
    new_id = ctx.free_adapter_id(f"{base[:30]}-{area}")
    if not CONNECTION_ID_PATTERN.fullmatch(new_id):
        raise HTTPException(status_code=422, detail="No valid ID could be made for the new data source")
    profiles, private = load_connection_profiles(), _load_private_secrets()
    _add_copy(profiles, private, source, area, new_id, (name or source["name"]).strip()[:120])
    _save_private_secrets(private)
    get_store().write_json("connections.json", {"connections": profiles})
    ctx.remember_counterpart(connection_id, area, new_id)
    return new_id


def split_adapter(ctx: SimpleNamespace, connection_id: str) -> dict[str, str]:
    """A data source used by every part becomes four separate ones. The Report Server keeps this one (published
    reports do not change); drafts, datasets and saved queries move to the one of their part."""
    from backend.app.analytics import store as analytics_store  # noqa: PLC0415 - analytics is optional
    profiles, private = load_connection_profiles(), _load_private_secrets()
    source = next((item for item in profiles if item["id"] == connection_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Data source not found: {connection_id}")
    if source.get("area"):
        raise HTTPException(status_code=409, detail=f"“{source['name']}” already belongs to {AREA_NAMES[source['area']]} only")
    ids = {"server": connection_id}
    for area in ("designer", "analytics", "query"):
        ids[area] = ctx.free_adapter_id(f"{connection_id[:30]}-{area}")
        _add_copy(profiles, private, source, area, ids[area], source["name"])
    source["area"] = "server"
    _save_private_secrets(private)
    get_store().write_json("connections.json", {"connections": profiles})
    ctx.remember_counterpart(ids["designer"], "server", connection_id)
    ctx.remember_counterpart(ids["query"], "designer", ids["designer"])
    ctx.remember_counterpart(ids["query"], "analytics", ids["analytics"])
    for key, data in ctx.workspace_entries():
        if connection_id in report_connection_ids(ctx, data):
            ctx.switch_connections(data, {connection_id: ids["designer"]})
            get_store().write_json(key, data)
    for load, save in ((analytics_store.datasets, analytics_store.save_dataset), (analytics_store.queries, analytics_store.save_query)):
        for item in load():
            if item.get("connectionId") == connection_id:
                save({**item, "connectionId": ids["analytics"]})
    return ids


# ------------------------------------------------------------------ the data sources of a report
def _datasets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    design = payload.get("design")
    found = design.get("datasets") if isinstance(design, dict) else None
    return [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []


def report_connection_ids(ctx: SimpleNamespace, payload: dict[str, Any]) -> list[str]:
    """The main query's data source (unless the report has no query) and those of its datasets."""
    main = [] if str(payload.get("queryLanguage") or "").lower() == "none" else [payload.get("connectionId")]
    return list(dict.fromkeys(str(value) for value in [*main, *(item.get("connectionId") for item in _datasets(payload))] if value))


def switch_connections(ctx: SimpleNamespace, payload: dict[str, Any], ids: dict[str, str]) -> None:
    if payload.get("connectionId") in ids:
        payload["connectionId"] = ids[payload["connectionId"]]
    for item in _datasets(payload):
        if item.get("connectionId") in ids:
            item["connectionId"] = ids[item["connectionId"]]


def draft_connections(ctx: SimpleNamespace, payload: dict[str, Any]) -> dict[str, str]:
    """Data sources of another part in a draft become their Designer counterpart ("" = choose one). -> what changed."""
    known = {item["id"]: item for item in load_connection_profiles()}
    used = {str(value) for value in (payload.get("connectionId"), *(item.get("connectionId") for item in _datasets(payload))) if value}
    changed = {key: ctx.counterpart(key, "designer") for key in used if key in known and not usable(known[key], "designer")}
    ctx.switch_connections(payload, changed)
    return changed


def adapter_uses(ctx: SimpleNamespace) -> dict[str, dict[str, list[str]]]:
    """{data source id: {"server": published reports, "designer": drafts, "analytics": datasets and saved queries}}"""
    from backend.app.analytics import store as analytics_store  # noqa: PLC0415 - analytics is optional
    out: dict[str, dict[str, list[str]]] = {}

    def add(connection_id: str, area: str, name: str) -> None:
        names = out.setdefault(connection_id, {}).setdefault(area, [])
        if name not in names:
            names.append(name)
    for connection_id, reports in ctx.adapter_usage().items():
        for report_id in reports:
            add(connection_id, "server", report_id)
    if get_store().has_dir("workspace") or get_store().keys("workspace"):      # reading must not seed the workspace
        for _, data in ctx.workspace_entries():
            for connection_id in report_connection_ids(ctx, data):
                add(connection_id, "designer", str(data.get("title") or data.get("id")))
    for item in [*analytics_store.datasets(), *analytics_store.queries()]:
        if item.get("connectionId"):
            add(str(item["connectionId"]), "analytics", str(item.get("name") or item["id"]))
    return out
