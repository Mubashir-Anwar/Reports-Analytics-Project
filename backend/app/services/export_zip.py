"""Report Server -> Export ZIP: every report (.mrd) and dashboard (.mdash) the user may manage, in the same folders as on
the server, with an index that names each item's server folder. No data, no data source passwords, no API keys."""
from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

ROOT = "server"
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
COLUMNS = ("serverFolder", "type", "id", "title", "dataSource", "datasets", "file")
README = """Report Server export
====================
server/        the reports and dashboards, in the same folders as on the server
index.html     open it in a browser: every folder and what is in it
index.csv      the same list for Excel
manifest.json  the same list for programs

.mrd    one report (query, parameters, design, pictures)        .mdash  one dashboard with its datasets' definitions
To put one back: Report Server -> Add Resource -> choose the file and the folder.
Not in here: data, data source passwords, users, schedules.
"""


def safe_name(text: str, fallback: str = "item") -> str:
    """A file or folder name that works on Windows, macOS and Linux."""
    name = re.sub(r'[<>:"/\|?*\x00-\x1f]', "_", str(text or "")).strip().rstrip(". ")[:100]
    name = name.lstrip(".") or fallback
    return f"_{name}" if name.split(".")[0].casefold() in RESERVED else name


def safe_folder(path: str) -> str:
    """ "General" is the server's top level. """
    if path == "General":
        return ""
    return "/".join(safe_name(part, "folder") for part in str(path or "").split("/") if part)


def _cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _index_page(entries: list[dict[str, Any]], folders: list[str], scope: str, when: str) -> str:
    by_folder: dict[str, list[dict[str, Any]]] = {folder: [] for folder in folders}
    for entry in entries:
        by_folder.setdefault(entry["serverFolder"], []).append(entry)
    parts = []
    for folder in sorted(by_folder, key=lambda name: (name != "/", name.casefold())):
        items = "".join(f"<li><span class='k {html.escape(e['type'])}'>{html.escape(e['type'])}</span>"
                        f"<a href='{html.escape(quote(e['file']))}'>{html.escape(e['title'] or e['id'])}</a> <code>{html.escape(e['id'])}</code></li>"
                        for e in by_folder[folder])
        parts.append(f"<section><h2>📁 {html.escape(folder)} <small>{len(by_folder[folder])} item(s)</small></h2>"
                     f"<ul>{items or '<li class=e>Empty folder</li>'}</ul></section>")
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Report Server export</title><style>:root{color-scheme:light dark}body{font:15px/1.5 system-ui,sans-serif;max-width:860px;"
            "margin:32px auto;padding:0 16px}section{border:1px solid #8884;border-radius:12px;padding:10px 18px;margin:12px 0}h2{font-size:16px;margin:4px 0}"
            "small,.e{opacity:.65;font-weight:400}ul{list-style:none;padding:0;margin:6px 0}li{padding:4px 0}.k{display:inline-block;min-width:82px;"
            "font-size:12px;opacity:.75}code{opacity:.6;font-size:12px}</style>"
            f"<h1>Report Server export</h1><p>{html.escape(scope)} · {html.escape(when)} · {len(entries)} item(s)</p>{''.join(parts)}</html>")


def build_zip(target: Any, reports: list[dict[str, Any]], dashboards: list[dict[str, Any]], folders: list[str], scope: str) -> dict[str, Any]:
    """Write the ZIP into a binary file. reports: {folder, id, title, dataSource, content};
    dashboards: {folder, id, title, datasets, content}. content is a callable, so one item is in memory at a time."""
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries: list[dict[str, Any]] = []
    taken: set[str] = set()

    def path_for(folder: str, stem: str, extension: str) -> str:
        base = "/".join(part for part in (ROOT, safe_folder(folder)) if part)
        name, number = f"{base}/{stem}{extension}", 2
        while name.casefold() in taken:
            name, number = f"{base}/{stem} ({number}){extension}", number + 1
        taken.add(name.casefold())
        return name

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", README)
        for folder in sorted(set(folders)):
            inside = safe_folder(folder)
            if inside:
                archive.writestr(f"{ROOT}/{inside}/", "")
        for kind, items, extension in (("report", reports, ".mrd"), ("dashboard", dashboards, ".mdash")):
            for item in items:
                name = path_for(item["folder"], safe_name(item["id"] if kind == "report" else item["title"], kind), extension)
                archive.writestr(name, item["content"]())
                entries.append({"serverFolder": "/" if item["folder"] == "General" else f"/{item['folder']}", "type": kind, "id": item["id"],
                                "title": item["title"], "dataSource": item.get("dataSource", ""), "datasets": item.get("datasets", ""), "file": name})
        entries.sort(key=lambda e: (e["serverFolder"].casefold(), e["type"], e["title"].casefold()))
        shown = ["/" if folder == "General" else f"/{folder}" for folder in folders]
        sheet = io.StringIO()
        writer = csv.writer(sheet)
        writer.writerow(("Server folder", "Type", "ID", "Title", "Data source", "Datasets", "File in this ZIP"))
        writer.writerows([_cell(entry[name]) for name in COLUMNS] for entry in entries)
        archive.writestr("index.csv", "﻿" + sheet.getvalue())
        archive.writestr("manifest.json", json.dumps({"format": "report-server-export", "version": 1, "exportedAt": when, "scope": scope,
                                                      "folders": shown, "items": entries}, ensure_ascii=False, indent=1))
        archive.writestr("index.html", _index_page(entries, shown, scope, when))
    return {"reports": sum(e["type"] == "report" for e in entries), "dashboards": sum(e["type"] == "dashboard" for e in entries)}
