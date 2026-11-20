"""A long report run must not freeze the other requests (pages, designer, API calls)."""
import asyncio
import sys
import time
from pathlib import Path

import httpx
from fastapi import Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service_names as core  # noqa: E402


def test_slow_async_handler_does_not_block_other_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    app = core.make_app()

    @app.post("/api/test-slow-export")
    async def slow_export(request: Request) -> dict:
        payload = await request.json()          # read in the worker from the cached body
        time.sleep(1.5)                         # blocking work, like rendering a big Excel file
        return {"echo": payload["n"]}

    @app.get("/api/test-quick")
    async def quick() -> dict:
        return {"ok": True}

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            started = time.monotonic()
            slow = asyncio.create_task(client.post("/api/test-slow-export", json={"n": 7}))
            await asyncio.sleep(0.2)
            fast = await client.get("/api/test-quick")
            fast_seconds = time.monotonic() - started   # with a blocked event loop this is the full 1.5 s+
            return await slow, fast, fast_seconds

    slow, fast, fast_seconds = asyncio.run(scenario())
    assert fast.status_code == 200 and fast_seconds < 0.8, fast_seconds
    assert slow.status_code == 200 and slow.json() == {"echo": 7}


def _poll(client, url, headers, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        item = client.get(url, headers=headers).json()["delivery"]
        if item["status"] in ("ready", "failed"):
            return item
        time.sleep(0.05)
    raise AssertionError(f"still {item['status']}")


def test_deliver_later_queues_runs_and_hands_the_file_to_the_caller_only(tmp_path, monkeypatch):
    """X-Deliver: later answers at once; several runs go at the same time; 'busy' waits in the queue; the file is
    only for the caller that asked, and a failure is reported on the delivery."""
    from fastapi import HTTPException
    from fastapi.responses import Response
    from fastapi.testclient import TestClient
    from backend.app.services import deliveries

    monkeypatch.setenv("REPORTING_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTING_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setattr(deliveries, "DEFERRABLE", deliveries.DEFERRABLE | {"/api/test-file/{name}", "/test-sync/{name}"})
    monkeypatch.setattr(deliveries, "DELIVERIES", deliveries.Deliveries(workers=4))
    monkeypatch.setattr(deliveries, "RETRY_SECONDS", 0.01)   # the queue retries quickly in the test
    app = core.make_app()
    busy = {"left": 2}

    @app.post("/api/test-file/{name}")
    async def make_file(name: str, request: Request) -> Response:
        payload = await request.json()
        if name == "busy" and busy["left"]:
            busy["left"] -= 1
            raise HTTPException(status_code=503, detail="busy", headers={"Retry-After": "1"})
        if name == "down":   # a database that cannot be reached is not "busy": no Retry-After, fails at once
            raise HTTPException(status_code=503, detail="MongoDB connection failed")
        if name == "bad":
            raise HTTPException(status_code=422, detail="Branch is required")
        time.sleep(0.3)
        return Response(f"{name}:{payload['n']}".encode(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})

    @app.get("/test-sync/{name}")
    def make_sync(name: str, request: Request) -> Response:
        return Response(b"sync", media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})

    me = {"X-Deliver": "later", "X-API-Key": "key-one"}
    other = {"X-API-Key": "key-two"}
    with TestClient(app) as client:
        started = time.monotonic()
        answers = [client.post(f"/api/test-file/r{i}", json={"n": i}, headers=me) for i in range(4)]
        assert time.monotonic() - started < 0.6          # answered at once, not after 4 × 0.3 s
        assert all(a.status_code == 202 for a in answers)
        items = [_poll(client, a.json()["delivery"]["statusUrl"], me) for a in answers]
        assert [i["status"] for i in items] == ["ready"] * 4
        assert time.monotonic() - started < 1.0          # made side by side
        file = client.get(items[2]["fileUrl"], headers=me)
        assert file.status_code == 200 and file.content == b"r2:2" and 'r2.csv' in file.headers["content-disposition"]
        assert client.get(items[2]["fileUrl"], headers=other).status_code == 404       # not someone else's
        assert client.get(items[2]["statusUrl"]).status_code == 404

        queued = _poll(client, client.post("/api/test-file/busy", json={"n": 1}, headers=me).json()["delivery"]["statusUrl"], me)
        assert queued["status"] == "ready" and queued["waitedForPlace"] is True

        bad = _poll(client, client.post("/api/test-file/bad", json={"n": 1}, headers=me).json()["delivery"]["statusUrl"], me)
        assert bad["status"] == "failed" and bad["statusCode"] == 422 and "Branch" in bad["error"]
        assert client.get(f"/api/deliveries/{bad['id']}/file", headers=me).status_code == 422

        down = _poll(client, client.post("/api/test-file/down", json={"n": 1}, headers=me).json()["delivery"]["statusUrl"], me)
        assert down["status"] == "failed" and down["statusCode"] == 503 and down["waitedForPlace"] is False

        sync = _poll(client, client.get("/test-sync/s1", headers=me).json()["delivery"]["statusUrl"], me)
        assert client.get(sync["fileUrl"], headers=me).content == b"sync"

        # without the header the same route answers with the file itself
        direct = client.post("/api/test-file/r9", json={"n": 9}, headers=other)
        assert direct.status_code == 200 and direct.content == b"r9:9"
        listed = client.get("/api/deliveries", headers=me).json()["deliveries"]
        assert len(listed) == 8 and client.get("/api/deliveries", headers=other).json()["deliveries"] == []
        assert client.delete(f"/api/deliveries/{sync['id']}", headers=me).json()["deleted"] == sync["id"]
