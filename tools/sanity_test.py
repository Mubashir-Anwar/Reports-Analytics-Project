"""Sanity, load and security test of a running server. Use a TEST server, never production: it creates users, API
keys, a data source, reports and a dashboard (all named sanity_* / SANITY_*), and it hammers the server.

    python tools/sanity_test.py setup    --base https://127.0.0.1:8780 --admin setup_admin --mongo-uri mongodb://user:pw@host/db
    python tools/sanity_test.py load     --base https://127.0.0.1:8780 --stages 10,50,100,250,500,1000 --seconds 60 --pid 1234
    python tools/sanity_test.py load     --base http://127.0.0.1:8001,http://127.0.0.1:8002   (several instances, no nginx)
    python tools/sanity_test.py security --base https://127.0.0.1:8780 --secret <text that must never show>...

The admin password is read from SANITY_ADMIN_PASSWORD (never from the command line). setup writes sanity_state.json
(bot names, API keys, report and dashboard IDs) next to --out; load and security read it.

load: virtual users join in stages (e.g. 10 → 1000). Each one signs in once and then, until the stage ends, repeats a
mix like real people: Designer previews, Report Server runs (CSV / Excel / PDF), dashboards, and websites that pull
reports by URL with API keys (some "deliver later": answered at once, made in the background, collected when ready).
Per stage it writes requests a second, p50 / p95 / p99 times, errors and 429 / 503 (the server protecting itself),
the queue wait of deliveries, and the server's memory and CPU (--pid).

security: anonymous calls to every route of the OpenAPI list, a low-rights user against every admin route, API keys
outside their folders, NoSQL / SQL injection in parameters and queries, path traversal, cookie and header flags,
user enumeration at sign-in, and a scan of every answer and every page / JavaScript file for --secret texts
(passwords, database URLs, keys). Findings are listed as FAIL / WARN / PASS.
Needs only httpx (already used by the tests)."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

FOLDER = "Sanity"
SOURCE = "sanity_mongo"
BRANCHES = ["LHR", "KHI", "ISB", "MUX", "PEW", "QTA", "FSD", "SKT"]
PLACEHOLDER_PASSWORDS = {"password", "pw", "pass", "key", "secret", "pwd", "passwd", "your_password", "yourpassword"}
BOT_PASSWORD = "Bot-pass-" + "7" * 12
OWNER_PASSWORD = os.environ.get("SANITY_OWNER_PASSWORD", BOT_PASSWORD + "Own")
QUERY_ONE = '[{"$match": {"Branch": {"$param": "Branch"}}}, {"$sort": {"OrderNo": 1}}, {"$limit": 200}, {"$project": {"_id": 0, "OrderNo": 1, "Name": 1, "Branch": 1, "Amount": 1}}]'
QUERY_BIG = '[{"$match": {"Branch": {"$param": "Branch"}}}, {"$limit": 9000}, {"$project": {"_id": 0, "OrderNo": 1, "Name": 1, "Branch": 1, "Amount": 1, "Qty": 1}}]'
DESIGN = {"bands": {"columnHeader": {"height": 20, "elements": [
    {"type": "staticText", "x": 0, "y": 0, "width": 80, "height": 16, "text": "Order"},
    {"type": "staticText", "x": 90, "y": 0, "width": 200, "height": 16, "text": "Name"},
    {"type": "staticText", "x": 300, "y": 0, "width": 100, "height": 16, "text": "Amount"}]},
    "detail": {"height": 18, "elements": [
        {"type": "textField", "x": 0, "y": 0, "width": 80, "height": 16, "expression": "$F{OrderNo}"},
        {"type": "textField", "x": 90, "y": 0, "width": 200, "height": 16, "expression": "$F{Name}"},
        {"type": "textField", "x": 300, "y": 0, "width": 100, "height": 16, "expression": "$F{Amount}"}]}}}


def definition(report_id: str, title: str, query: str) -> dict:
    return {"id": report_id, "title": title, "description": "Made by tools/sanity_test.py", "folder": FOLDER, "connectionId": SOURCE,
            "collection": "orders", "queryLanguage": "aggregation-json", "querySource": query,
            "columns": ["OrderNo", "Name", "Branch", "Amount"], "formats": ["pdf", "xlsx", "csv"], "defaultFormat": "csv",
            "parameters": [{"name": "Branch", "type": "string", "label": "Branch", "required": True}], "design": DESIGN}


# ------------------------------------------------------------------------------------------------ helpers
def client(base: str, verify: bool, **extra: Any) -> httpx.AsyncClient:
    limits = httpx.Limits(max_connections=extra.pop("max_connections", 50), max_keepalive_connections=20)
    return httpx.AsyncClient(base_url=base, verify=verify, timeout=httpx.Timeout(180, connect=30), limits=limits,
                             headers={"User-Agent": "sanity-test"}, **extra)


async def sign_in(http: httpx.AsyncClient, username: str, password: str) -> httpx.Response:
    return await http.post("/api/server/login", json={"username": username, "password": password})


def must(response: httpx.Response, *ok: int) -> dict:
    if response.status_code not in (ok or (200, 201)):
        raise SystemExit(f"{response.request.method} {response.request.url.path} -> {response.status_code}: {response.text[:300]}")
    return response.json() if response.content and "json" in response.headers.get("content-type", "") else {}


class ProcessWatch:
    """Memory (MB) and CPU (% of one core) of the server process, sampled every second (Windows or Linux, no packages)."""

    def __init__(self, pid: int) -> None:
        self.pid, self.samples, self._last = pid, [], None

    def _read(self) -> tuple[float, float]:
        if os.name == "nt":
            class Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                            "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                            "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
            handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0400, False, self.pid)
            if not handle:
                return 0.0, 0.0
            try:
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
                times = [ctypes.c_ulonglong() for _ in range(4)]
                ctypes.windll.kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times])
                return counters.WorkingSetSize / 1048576, (times[2].value + times[3].value) / 1e7
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        status = Path(f"/proc/{self.pid}/status").read_text()
        rss = int(re.search(r"VmRSS:\s+(\d+)", status).group(1)) / 1024
        fields = Path(f"/proc/{self.pid}/stat").read_text().rsplit(")", 1)[1].split()
        return rss, (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                memory, cpu_seconds = self._read()
                now = time.monotonic()
                cpu = 0.0 if self._last is None else 100 * (cpu_seconds - self._last[1]) / max(1e-6, now - self._last[0])
                self._last = (now, cpu_seconds)
                self.samples.append({"t": now, "mb": round(memory, 1), "cpu": round(cpu, 1)})
            except (OSError, AttributeError, ValueError):
                pass
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except asyncio.TimeoutError:
                pass


# ------------------------------------------------------------------------------------------------ setup
async def setup(args: argparse.Namespace) -> None:
    password = os.environ.get("SANITY_ADMIN_PASSWORD", "")
    if not password:
        raise SystemExit("Set SANITY_ADMIN_PASSWORD (the password of --admin)")
    state: dict[str, Any] = {"base": args.base, "bots": [], "keys": []}
    async with client(args.base, not args.insecure) as http:
        me = must(await sign_in(http, args.admin, password))
        print("signed in as", me.get("username", args.admin))
        # a lasting super admin (the checklist wants one besides the temporary setup admin)
        owner = {"username": "sanity_owner", "password": OWNER_PASSWORD, "superAdmin": True, "email": "owner@sanity.test", "fullName": "Sanity owner"}
        response = await http.post("/api/admin/users", json=owner)
        if response.status_code not in (200, 201, 409):
            must(response)
        # the temporary setup admin is removed once a lasting super admin exists: go on as that one
        must(await sign_in(http, owner["username"], owner["password"]))
        state["admin"] = owner["username"]
        # data source, folder, reports
        await http.request("DELETE", f"/api/data-sources/{SOURCE}", json={})
        must(await http.post("/api/data-sources", json={"id": SOURCE, "name": "Sanity Mongo", "type": "mongodb", "uri": args.mongo_uri,
                                                        "database": args.mongo_db, "folder": ""}))
        for path in ("/api/admin/folders", "/api/admin/workspace-folders"):
            await http.post(path, json={"path": FOLDER})
        reports = {"SANITY_SMALL": ("Sanity 200 rows", QUERY_ONE), "SANITY_BIG": ("Sanity whole branch", QUERY_BIG)}
        for report_id, (title, query) in reports.items():
            saved = await http.put(f"/api/admin/workspace/{report_id}?create=true", json=definition(report_id, title, query))
            if saved.status_code == 409:              # there from an earlier run: save over it
                saved = await http.put(f"/api/admin/workspace/{report_id}?folder={FOLDER}", json=definition(report_id, title, query))
            must(saved)
            must(await http.post(f"/api/admin/workspace/{report_id}/publish", json={"folder": FOLDER, "replace": True}))
        state["reports"] = list(reports)
        must(await http.put("/api/server/permissions", json={"path": FOLDER, "rules": {"users": {}, "roles": {"viewer": "run", "designer": "run"}}}))
        # analytics dataset + dashboard
        dataset = must(await http.post("/api/analytics/datasets", json={
            "name": "Sanity orders", "folder": FOLDER, "connectionId": SOURCE, "queryLanguage": "aggregation-json", "collection": "orders",
            "querySource": '[{"$project": {"_id": 0, "Branch": 1, "Amount": 1, "Qty": 1, "Day": 1}}]', "refreshEveryMinutes": 0}), 200, 201)["dataset"]
        for _ in range(240):
            items = must(await http.get("/api/analytics/datasets"))["datasets"]
            status = next((d["status"] for d in items if d["id"] == dataset["id"]), "")
            if status in ("ready", "failed"):
                break
            await asyncio.sleep(1)
        print("dataset", dataset["id"], status)
        tiles = [{"id": "kpi", "type": "kpi", "title": "Total", "x": 0, "y": 0, "w": 3, "h": 2, "dataset": dataset["id"], "values": [{"column": "Amount", "agg": "sum"}]},
                 {"id": "bar", "type": "column", "title": "By branch", "x": 3, "y": 0, "w": 9, "h": 5, "dataset": dataset["id"],
                  "groupBy": [{"column": "Branch"}], "values": [{"column": "Amount", "agg": "sum"}, {"column": "Qty", "agg": "avg"}]}]
        dashboard = must(await http.post("/api/analytics/dashboards", json={"name": "Sanity dashboard", "folder": FOLDER, "tiles": tiles}), 200, 201)["dashboard"]
        state.update(dataset=dataset["id"], dashboard=dashboard["id"], tiles=tiles)
        # API keys (one per "website")
        for n in range(args.keys):
            name = f"sanity_site_{n:02d}"
            listed = must(await http.get("/api/admin/api-keys")).get("keys", [])
            for item in listed:
                if item.get("name") == name:
                    await http.request("DELETE", f"/api/admin/api-keys/{item['id']}", json={})
            created = must(await http.post("/api/admin/api-keys", json={"name": name, "folders": [FOLDER]}), 200, 201)
            state["keys"].append(created["secret"])
        # a key limited to another folder, for the security test
        created = must(await http.post("/api/admin/api-keys", json={"name": f"sanity_other_{int(time.time())}", "folders": ["NotSanity"]}), 200, 201)
        state["otherKey"] = created["secret"]
        # bot users: a mix of rights like real people
        kinds = ["designer", "server", "analytics", "query"]
        semaphore = asyncio.Semaphore(8)

        async def make_bot(n: int) -> None:
            kind = kinds[n % len(kinds)]
            rights = {"designer": ["designer", "server"], "server": ["server"], "analytics": ["analytics", "server"], "query": ["query"]}[kind]
            name = f"sanity_bot_{n:04d}"
            async with semaphore:
                response = await http.post("/api/admin/users", json={"username": name, "password": BOT_PASSWORD, "rights": rights,
                                                                     "email": f"{name}@sanity.test", "fullName": f"Bot {n}"})
                if response.status_code == 409:      # there from an earlier run: make sure its rights are what this run expects
                    response = await http.put(f"/api/admin/users/{name}", json={"rights": rights})
                if response.status_code not in (200, 201):
                    print("bot", name, response.status_code, response.text[:120])
                    return
            state["bots"].append({"username": name, "kind": kind})

        started = time.monotonic()
        await asyncio.gather(*(make_bot(n) for n in range(args.users)))
        print(f"{len(state['bots'])} bot users in {time.monotonic() - started:.1f} s")
        # one low-rights user for the rights tests
        await http.post("/api/admin/users", json={"username": "sanity_viewer", "password": BOT_PASSWORD, "rights": [], "email": "v@sanity.test"})
    Path(args.state).write_text(json.dumps(state, indent=1))
    print("state written to", args.state)


# ------------------------------------------------------------------------------------------------ load
class Stats:
    def __init__(self) -> None:
        self.calls: list[tuple[float, str, int, float, int]] = []      # (when, operation, status, seconds, bytes)
        self.waits: list[dict] = []

    def add(self, op: str, status: int, seconds: float, size: int = 0) -> None:
        self.calls.append((time.monotonic(), op, status, seconds, size))


async def timed(stats: Stats, op: str, call) -> httpx.Response | None:
    started = time.monotonic()
    try:
        response = await call
    except httpx.HTTPError as exc:
        stats.add(op, 0, time.monotonic() - started)
        stats.add(f"{op}!{type(exc).__name__}", 0, 0)
        return None
    stats.add(op, response.status_code, time.monotonic() - started, len(response.content))
    return response


async def deliver_later(http: httpx.AsyncClient, stats: Stats, report: str, fmt: str, key: str) -> None:
    started = time.monotonic()
    auth = {"X-API-Key": key}
    response = await timed(stats, "key: deliver later (ask)", http.post(f"/api/v1/reports/{report}/run?folder={FOLDER}", json={
        "format": fmt, "parameters": {"Branch": random.choice(BRANCHES)}}, headers={**auth, "X-Deliver": "later"}))
    if response is None or response.status_code != 202:
        return
    status_url = response.json()["delivery"]["statusUrl"]
    while time.monotonic() - started < 600:
        await asyncio.sleep(1)
        polled = await http.get(status_url, headers=auth)
        if polled.status_code != 200:
            continue
        delivery = polled.json()["delivery"]
        if delivery["status"] in ("ready", "failed"):
            file = await timed(stats, "key: deliver later (file)", http.get(delivery["fileUrl"], headers=auth)) if delivery["status"] == "ready" else None
            stats.waits.append({"format": fmt, "status": delivery["status"], "total": time.monotonic() - started,
                                "queued": delivery.get("waitedForPlace"), "bytes": len(file.content) if file is not None else 0})
            return
    stats.waits.append({"format": fmt, "status": "timeout", "total": time.monotonic() - started})


async def virtual_user(n: int, bot: dict, state: dict, args: argparse.Namespace, stats: Stats, until: float) -> None:
    """One person until `until`: signs in, then repeats what their rights allow, with a pause between clicks."""
    key = state["keys"][n % len(state["keys"])]
    bases = args.base.split(",")             # several instances: one person always uses the same one (like nginx hash)
    async with client(bases[n % len(bases)], not args.insecure, max_connections=4) as http:
        response = await timed(stats, "sign in", sign_in(http, bot["username"], BOT_PASSWORD))
        if response is None or response.status_code != 200:
            return
        await timed(stats, "home", http.get("/api/home"))
        while time.monotonic() < until:
            branch = random.choice(BRANCHES)
            roll = random.random()
            kind = bot["kind"]
            if roll < 0.30:      # a website pulls a report by URL with its API key
                fmt = random.choice(["csv", "csv", "xlsx", "pdf"])
                await timed(stats, f"key: /generate {fmt}", http.get(f"/generate/SANITY_SMALL.{fmt}?Branch={branch}",
                                                                    headers={"X-API-Key": key}))
            elif roll < 0.38:
                await deliver_later(http, stats, random.choice(state["reports"]), random.choice(["csv", "xlsx", "pdf"]), key)
            elif kind == "designer":
                await timed(stats, "designer: preview", http.post("/api/admin/drafts/preview", json={
                    "definition": definition("SANITY_SMALL", "Preview", QUERY_ONE), "parameters": {"Branch": branch}, "connectionId": SOURCE}))
            elif kind == "server":
                report, fmt = random.choice([("SANITY_SMALL", "pdf"), ("SANITY_SMALL", "xlsx"), ("SANITY_BIG", "csv")])
                await timed(stats, f"server: {report} {fmt}", http.post(f"/api/server/reports/{report}/run?folder={FOLDER}",
                                                                         json={"format": fmt, "parameters": {"Branch": branch}}))
            elif kind == "analytics":
                queries = [{"dataset": state["dataset"], "values": tile["values"], "groupBy": tile.get("groupBy", [])} for tile in state["tiles"]]
                await timed(stats, "dashboard: visuals", http.post(f"/api/server/dashboards/{state['dashboard']}/query-batch", json={
                    "queries": queries, "filters": [{"dataset": state["dataset"], "column": "Branch", "op": "in", "values": [branch]}]}))
            else:
                await timed(stats, "query: aggregate", http.post("/api/query/mongo/aggregate", json={
                    "connectionId": SOURCE, "collection": "orders", "limit": 50,
                    "stages": [f'{{"$match": {{"Branch": "{branch}"}}}}', '{"$group": {"_id": "$Name", "n": {"$sum": 1}}}', '{"$sort": {"n": -1}}']}))
            await asyncio.sleep(random.uniform(args.think / 2, args.think * 1.5))


def defaultdict_count(values) -> dict:
    out: dict = defaultdict(int)
    for value in values:
        out[value] += 1
    return out


def summarize(stage: int, stats: Stats, started: float, ended: float, watch: ProcessWatch | None) -> dict:
    window = [c for c in stats.calls if started <= c[0] <= ended + 600 and "!" not in c[1]]
    by_op: dict[str, list] = defaultdict(list)
    for call in window:
        by_op[call[1]].append(call)
    ops = {}
    for op, calls in sorted(by_op.items()):
        times = sorted(c[3] for c in calls)
        pct = lambda p: round(times[min(len(times) - 1, int(p * len(times)))] * 1000)  # noqa: E731
        ops[op] = {"n": len(calls), "ok": sum(200 <= c[2] < 300 for c in calls), "busy429": sum(c[2] == 429 for c in calls),
                   "busy503": sum(c[2] == 503 for c in calls), "errors": sum(c[2] == 0 or c[2] >= 500 and c[2] != 503 for c in calls),
                   "other4xx": sum(400 <= c[2] < 500 and c[2] != 429 for c in calls),
                   "codes": dict(sorted(defaultdict_count(c[2] for c in calls).items())), "p50": pct(.5), "p95": pct(.95), "p99": pct(.99), "max": round(times[-1] * 1000), "mb": round(sum(c[4] for c in calls) / 1048576, 1)}
    exceptions = defaultdict(int)
    for call in stats.calls:
        if "!" in call[1] and started <= call[0] <= ended + 600:
            exceptions[call[1]] += 1
    samples = [s for s in (watch.samples if watch else []) if started <= s["t"] <= ended]
    return {"users": stage, "seconds": round(ended - started, 1), "requests": len(window), "rps": round(len(window) / max(1, ended - started), 1),
            "ops": ops, "exceptions": dict(exceptions),
            "memoryMB": {"start": samples[0]["mb"], "peak": max(s["mb"] for s in samples), "end": samples[-1]["mb"]} if samples else {},
            "cpu": {"avg": round(statistics.mean(s["cpu"] for s in samples), 1), "peak": max(s["cpu"] for s in samples)} if samples else {},
            "deliveries": stats.waits[:]}


async def load(args: argparse.Namespace) -> None:
    state = json.loads(Path(args.state).read_text())
    bots = state["bots"]
    watch = ProcessWatch(args.pid) if args.pid else None
    stop = asyncio.Event()
    watcher = asyncio.create_task(watch.run(stop)) if watch else None
    results = []
    for stage in [int(x) for x in args.stages.split(",")]:
        stats = Stats()
        started = time.monotonic()
        until = started + args.seconds
        users = []
        for n in range(min(stage, len(bots))):          # people arrive over the first quarter of the stage, not all in one go
            users.append(asyncio.create_task(virtual_user(n, bots[n], state, args, stats, until)))
            await asyncio.sleep(args.seconds / 4 / max(1, stage))
        await asyncio.gather(*users)
        ended = time.monotonic()
        summary = summarize(stage, stats, started, ended, watch)
        results.append(summary)
        print(format_stage(summary), flush=True)
        Path(args.out).write_text(json.dumps(results, indent=1))
        failed = sum(op["errors"] for op in summary["ops"].values())
        if failed > summary["requests"] * 0.5:
            print("more than half of the requests failed: stopping here")
            break
        await asyncio.sleep(args.rest)
    stop.set()
    if watcher:
        await watcher
    Path(args.out).write_text(json.dumps(results, indent=1))
    print("results written to", args.out)


def format_stage(s: dict) -> str:
    lines = [f"\n=== {s['users']} users · {s['seconds']} s · {s['requests']} requests · {s['rps']} req/s · "
             f"memory {s.get('memoryMB')} · cpu {s.get('cpu')}"]
    for op, o in s["ops"].items():
        lines.append(f"  {op:32} n={o['n']:6} ok={o['ok']:6} 429={o['busy429']:5} 503={o['busy503']:5} err={o['errors']:4} 4xx={o['other4xx']:4} "
                     f"p50={o['p50']:6} p95={o['p95']:6} p99={o['p99']:6} max={o['max']:6} ms  codes={o['codes']}")
    if s["exceptions"]:
        lines.append(f"  exceptions: {s['exceptions']}")
    waits = s["deliveries"]
    if waits:
        done = [w for w in waits if w["status"] == "ready"]
        lines.append(f"  deliver later: {len(done)}/{len(waits)} ready, queued {sum(1 for w in waits if w.get('queued'))}, "
                     f"total wait p50={round(statistics.median([w['total'] for w in done]), 1) if done else '-'} s "
                     f"max={round(max([w['total'] for w in done]), 1) if done else '-'} s")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ security
class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, level: str, area: str, text: str) -> None:
        self.items.append((level, area, text))
        print(f"[{level}] {area}: {text}", flush=True)


PUBLIC_OK = re.compile(r"^/(health|ready|login|reset-password|favicon\.ico|signout\.js|assets/.*|embed/.*|api/server/(login|logout|password-help|forgot-password|reset-password|me|branding)|api/embed/.*|api/branding.*)$")


def fill_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "SANITY_SMALL", path)


async def security(args: argparse.Namespace) -> None:
    state = json.loads(Path(args.state).read_text())
    found = Findings()
    secrets_to_hide = [s for s in args.secret if s]

    def scan(where: str, text: str) -> None:
        for secret in secrets_to_hide:
            if secret in text:
                found.add("FAIL", "secret shown", f"{where} contains a secret ({secret[:4]}…)")
        for pattern, what in ((r"mongodb(?:\+srv)?://[^\s\"'`/:@]*:([^\s\"'`@/]+)@", "a MongoDB URL with a password"),
                              (r"(?:postgres(?:ql)?|mysql|mssql|sqlserver|oracle)[a-z+]*://[^\s\"'`/:@]*:([^\s\"'`@/]+)@",
                               "a SQL URL with a password")):
            # example texts in input hints (user:password@host) and masked values (u:***@h) are not secrets
            real = [pw for pw in re.findall(pattern, text) if pw.strip("<>{}").lower() not in PLACEHOLDER_PASSWORDS and set(pw) - set("*…x")]
            if real:
                found.add("FAIL", "secret shown", f"{where} contains {what}")

    async with client(args.base, not args.insecure) as anon, client(args.base, not args.insecure) as admin, \
            client(args.base, not args.insecure) as viewer:
        must(await sign_in(admin, state.get("admin", "sanity_owner"), OWNER_PASSWORD))
        must(await sign_in(viewer, "sanity_viewer", BOT_PASSWORD))
        # 1. what an anonymous visitor and a low-rights user can reach
        spec = await admin.get("/openapi.json")
        anon_spec = await anon.get("/openapi.json")
        found.add("PASS" if anon_spec.status_code in (401, 403, 404) else "FAIL", "open API", f"/openapi.json signed out -> {anon_spec.status_code}")
        docs = await anon.get("/docs")
        found.add("PASS" if docs.status_code in (401, 403, 404) or "/login" in docs.headers.get("location", "") else "FAIL", "open API",
                  f"/docs signed out -> {docs.status_code}")
        routes = []
        if spec.status_code == 200:
            for path, methods in spec.json().get("paths", {}).items():
                for method in methods:
                    routes.append((method.upper(), path))
        else:
            found.add("WARN", "open API", f"admin cannot read /openapi.json ({spec.status_code}); docs area is off, so routes come from a fixed list")
        print(f"{len(routes)} routes to probe")
        open_routes, breaches = [], []
        for method, path in routes:
            url = fill_path(path)
            for who, http in (("anonymous", anon), ("viewer", viewer)):
                try:
                    response = await http.request(method, url, json={} if method in ("POST", "PUT", "PATCH", "DELETE") else None,
                                                  headers={"Content-Type": "application/json"}, follow_redirects=False)
                except httpx.HTTPError:
                    continue
                scan(f"{who} {method} {path}", response.text[:200000])
                if who == "anonymous" and response.status_code < 300 and not PUBLIC_OK.match(url):
                    open_routes.append(f"{method} {path} -> {response.status_code}")
                if who == "viewer" and response.status_code < 300 and (path.startswith("/api/admin/") or path.startswith("/api/data-sources")) \
                        and method != "GET" and not path.startswith(("/api/admin/whoami",)):
                    breaches.append(f"{method} {path} -> {response.status_code}")
        found.add("FAIL" if open_routes else "PASS", "open API", f"{len(open_routes)} routes answer without sign-in" + (f": {open_routes[:15]}" if open_routes else ""))
        found.add("FAIL" if breaches else "PASS", "rights", f"{len(breaches)} admin writes worked for a user without rights" + (f": {breaches[:15]}" if breaches else ""))
        # 2. privilege escalation attempts by the low-rights user
        me = (await viewer.get("/api/server/me")).json()
        attempts = [("PUT", "/api/server/account", {"rights": ["admin"], "superAdmin": True, "role": "admin"}),
                    ("POST", "/api/admin/users", {"username": "sanity_evil", "password": BOT_PASSWORD, "superAdmin": True, "email": "e@x.test"}),
                    ("PUT", "/api/admin/users/sanity_viewer", {"rights": ["admin"], "superAdmin": True}),
                    ("POST", "/api/admin/api-keys", {"name": "evil", "folders": []}),
                    ("POST", "/api/data-sources", {"id": "evil", "type": "mongodb", "uri": "mongodb://127.0.0.1:27017", "database": "admin"})]
        for method, path, body in attempts:
            response = await viewer.request(method, path, json=body)
            after = (await viewer.get("/api/server/me")).json()
            escalated = set(after.get("rights", [])) - set(me.get("rights", [])) or after.get("superAdmin") and not me.get("superAdmin")
            ok = response.status_code in (401, 403, 404, 405, 422) or (response.status_code < 300 and not escalated and path == "/api/server/account")
            found.add("PASS" if ok and not escalated else "FAIL", "rights", f"viewer {method} {path} -> {response.status_code}{' and got more rights' if escalated else ''}")
        # admin rights for the API: a key must never reach admin routes, and only its folders
        key, other = state["keys"][0], state["otherKey"]
        for method, path in (("GET", "/api/admin/users"), ("GET", "/api/data-sources"), ("GET", "/api/admin/api-keys"), ("GET", "/api/admin/system/status")):
            response = await anon.request(method, path, headers={"X-API-Key": key})
            found.add("PASS" if response.status_code in (401, 403) else "FAIL", "API key", f"key on {path} -> {response.status_code}")
        response = await anon.get(f"/generate/SANITY_SMALL.csv?Branch=LHR&folder={FOLDER}", headers={"X-API-Key": other})
        found.add("PASS" if response.status_code in (403, 404) else "FAIL", "API key", f"key of another folder runs a Sanity report -> {response.status_code}")
        response = await anon.get(f"/generate/SANITY_SMALL.csv?Branch=LHR&folder={FOLDER}", headers={"X-API-Key": key[:-4] + "abcd"})
        found.add("PASS" if response.status_code == 401 else "FAIL", "API key", f"a changed key -> {response.status_code}")
        response = await anon.get(f"/generate/SANITY_SMALL.csv?Branch=LHR&folder={FOLDER}&api_key={key}")
        found.add("PASS" if response.status_code == 401 else "WARN", "API key", f"key in the URL (ends up in logs) -> {response.status_code}")
        # 3. injection
        injections = [{"$ne": None}, {"$gt": ""}, {"$where": "sleep(2000) || true"}, "LHR' OR '1'='1", "LHR\"; DROP TABLE x; --", "../../etc/passwd",
                      {"$regex": ".*"}, ["LHR", "KHI"], "LHR\u0000"]
        for value in injections:
            started = time.monotonic()
            response = await anon.post(f"/api/v1/reports/SANITY_SMALL/run?folder={FOLDER}", headers={"X-API-Key": key},
                                       json={"format": "json", "parameters": {"Branch": value}})
            rows = len(response.json().get("rows", [])) if response.status_code == 200 and "json" in response.headers.get("content-type", "") else -1
            slow = time.monotonic() - started > 1.8
            leaked = rows > 0 and not isinstance(value, str) and value != ["LHR", "KHI"]
            found.add("FAIL" if leaked or slow else "PASS", "injection",
                      f"Branch={json.dumps(value)[:40]} -> {response.status_code}, rows={rows}{' (ran slow: $where executed?)' if slow else ''}")
        dangerous = ['{"$out": "sanity_pwned"}', '{"$merge": {"into": "sanity_pwned"}}', '{"$function": {"body": "function(){return 1}", "args": [], "lang": "js"}}',
                     '{"$lookup": {"from": "system.users", "localField": "x", "foreignField": "y", "as": "z"}}', '{"$currentOp": {}}']
        for stage in dangerous:
            response = await admin.post("/api/query/mongo/aggregate", json={"connectionId": SOURCE, "collection": "orders", "limit": 5,
                                                                            "stages": ['{"$limit": 1}', stage]})
            found.add("PASS" if response.status_code in (400, 403, 422) else "FAIL", "injection", f"Query aggregate with {stage[:28]}… -> {response.status_code}")
            definition_bad = definition("SANITY_EVIL", "Evil", f'[{{"$limit": 1}}, {stage}]')
            response = await admin.post("/api/admin/drafts/preview", json={"definition": definition_bad, "parameters": {"Branch": "LHR"}, "connectionId": SOURCE})
            found.add("PASS" if response.status_code in (400, 403, 422) else "FAIL", "injection", f"report query with {stage[:28]}… -> {response.status_code}")
        # 4. path traversal
        for path in ("/generate/..%2f..%2fbackend%2fapp%2fmain.py", "/assets/..%2f..%2f.env", "/assets/../../.env", "/api/v1/reports/..%2f..%2fusers/run",
                     "/api/deliveries/..%2f..%2fusers.json/file", "/embed/..%2f..%2f.env"):
            response = await admin.get(path, headers={"X-API-Key": key})
            body = response.text[:2000]
            bad = response.status_code == 200 and ("REPORTING_" in body or "def " in body or "passwordHash" in body)
            found.add("FAIL" if bad else "PASS", "path traversal", f"{path} -> {response.status_code}")
        # 5. sign-in: enumeration, lock, cookies and headers
        # a fresh name each run, and a known account with no failed tries yet (signing in clears its count)
        must(await sign_in(viewer, "sanity_viewer", BOT_PASSWORD))
        unknown_name = f"no_such_user_{random.randrange(10**9)}"
        timings: dict[str, list[float]] = {"unknown": [], "known": []}
        answers = {}
        for name, label in ((unknown_name, "unknown"), ("sanity_viewer", "known")) * 3:
            started = time.monotonic()
            answers[label] = await anon.post("/api/server/login", json={"username": name, "password": "wrong-password-1"})
            timings[label].append(time.monotonic() - started)
        must(await sign_in(viewer, "sanity_viewer", BOT_PASSWORD))          # clears the three wrong tries again
        unknown, known = answers["unknown"], answers["known"]
        same = unknown.status_code == known.status_code and unknown.json().get("detail") == known.json().get("detail")
        found.add("PASS" if same else "WARN", "sign-in", f"unknown vs known user answers {'look the same' if same else 'differ: ' + str(unknown.json().get('detail'))[:60] + ' | ' + str(known.json().get('detail'))[:60]}")
        slow_known, slow_unknown = statistics.median(timings["known"]), statistics.median(timings["unknown"])
        close = abs(slow_known - slow_unknown) < max(0.05, 0.5 * max(slow_known, slow_unknown))
        found.add("PASS" if close else "FAIL", "sign-in", f"wrong password takes {slow_known * 1000:.0f} ms for a real name and "
                  f"{slow_unknown * 1000:.0f} ms for an unknown one ({'same' if close else 'the time tells which names exist'})")
        login = await sign_in(viewer, "sanity_viewer", BOT_PASSWORD)
        cookie = login.headers.get("set-cookie", "")
        for flag in ("HttpOnly", "Secure", "SameSite"):
            found.add("PASS" if flag.lower() in cookie.lower() else "FAIL", "cookie", f"session cookie {flag}: {'yes' if flag.lower() in cookie.lower() else 'no'}")
        page = await admin.get("/designer")
        for header in ("content-security-policy", "x-frame-options", "x-content-type-options", "strict-transport-security", "referrer-policy"):
            found.add("PASS" if header in page.headers else "WARN", "headers", f"{header}: {page.headers.get(header, 'missing')[:60]}")
        for header in ("server", "x-powered-by"):
            if header in page.headers and re.search(r"\d", page.headers[header]):
                found.add("WARN", "headers", f"{header} shows a version: {page.headers[header]}")
        # logout really ends the session
        cookie_jar = dict(viewer.cookies)
        await viewer.post("/api/server/logout", json={})
        async with client(args.base, not args.insecure, cookies=cookie_jar) as reuse:
            again = await reuse.get("/api/server/me")
        found.add("PASS" if again.status_code == 401 else "FAIL", "session", f"old cookie after sign-out -> {again.status_code}")
        # 6. what a browser gets: every page, script and the answers the pages read
        pages = ["/login", "/home", "/designer", "/query", "/server", "/admin", "/analytics"]
        scripts: set[str] = set()
        for path in pages:
            response = await admin.get(path)
            scan(f"page {path}", response.text)
            scripts.update(re.findall(r'(?:src|href)="(/assets/[^"]+\.(?:js|css))"', response.text))
        for _ in range(2):
            for path in list(scripts):
                body = (await admin.get(path)).text
                scan(f"file {path}", body)
                scripts.update("/assets/app/assets/" + name for name in re.findall(r'"\./([A-Za-z0-9_-]+-[A-Za-z0-9_-]{8}\.js)"', body))
        print(f"scanned {len(scripts)} script and style files")
        for path in ("/api/data-sources", f"/api/data-sources/{SOURCE}", "/api/admin/connections", "/api/admin/system/status", "/api/home",
                     "/api/server/me", "/api/admin/users", "/api/admin/api-keys", "/api/analytics/datasets", f"/api/analytics/datasets/{state['dataset']}",
                     "/api/admin/reports", "/api/admin/workspace/SANITY_SMALL", "/api/admin/logs?lines=2000", "/api/admin/audit-events?limit=500",
                     "/api/admin/audit?limit=500", "/api/deliveries", "/api/admin/storage", "/api/admin/system-db"):
            response = await admin.get(path)
            scan(f"answer {path} ({response.status_code})", response.text)
        response = await admin.post(f"/api/data-sources/{SOURCE}/test", json={})
        scan("answer test data source", response.text)
        bad_uri = args.mongo_uri.replace("127.0.0.1", "127.0.0.2").replace(":27017", ":27999")
        response = await admin.post("/api/data-sources/test", json={"type": "mongodb", "uri": bad_uri, "database": "x"})
        scan("error of a failing connection test", response.text)
        # 7. a big body is refused
        response = await admin.post("/api/admin/drafts/preview", content=b'{"x": "' + b"a" * (60 * 1024 * 1024) + b'"}', headers={"Content-Type": "application/json"})
        found.add("PASS" if response.status_code == 413 else "WARN", "limits", f"60 MB body -> {response.status_code}")
    # summary
    fails = sum(1 for item in found.items if item[0] == "FAIL")
    warns = sum(1 for item in found.items if item[0] == "WARN")
    Path(args.out).write_text(json.dumps([{"level": a, "area": b, "text": c} for a, b, c in found.items], indent=1))
    print(f"\n{fails} FAIL · {warns} WARN · {len(found.items) - fails - warns} PASS — written to {args.out}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["setup", "load", "security"])
    parser.add_argument("--base", default="https://127.0.0.1:8780", help="load: several instances as a,b,c")
    parser.add_argument("--insecure", action="store_true", help="accept a self-signed certificate (a test server)")
    parser.add_argument("--state", default="sanity_state.json")
    parser.add_argument("--out", default="sanity_result.json")
    parser.add_argument("--admin", default="setup_admin")
    parser.add_argument("--mongo-uri", default="mongodb://127.0.0.1:27017")
    parser.add_argument("--mongo-db", default="rs_sanity_data")
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--keys", type=int, default=20)
    parser.add_argument("--stages", default="10,50,100,250,500,1000")
    parser.add_argument("--seconds", type=int, default=60, help="length of each stage")
    parser.add_argument("--rest", type=int, default=10, help="pause between stages")
    parser.add_argument("--think", type=float, default=3.0, help="average pause between two clicks of one user (s)")
    parser.add_argument("--pid", type=int, default=0, help="server process ID, for memory and CPU")
    parser.add_argument("--secret", action="append", default=[], help="a text that must never show (repeat)")
    args = parser.parse_args()
    if args.insecure:
        import warnings
        warnings.filterwarnings("ignore")
    asyncio.run({"setup": setup, "load": load, "security": security}[args.command](args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
