"""Collect report files made in the background after "X-Deliver: later" (services/deliveries.py).

    GET    /api/deliveries               your downloads (waiting, running, ready, failed)
    GET    /api/deliveries/{id}          one download's status
    GET    /api/deliveries/{id}/file     the file (409 while it is still being made)
    DELETE /api/deliveries/{id}          forget it (and remove the file)

Only the caller that asked (same session cookie, API key or Authorization header) sees a download.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from backend.app.services import deliveries


def register(app: FastAPI, ctx: SimpleNamespace) -> None:  # noqa: ARG001 - same shape as the other routers
    @app.get("/api/deliveries", tags=["Downloads"])
    def list_deliveries(request: Request) -> dict[str, Any]:
        if not deliveries.has_credentials(request):
            raise HTTPException(status_code=401, detail="Sign in, or send your API key, to see your downloads")
        deliveries.DELIVERIES.sweep()
        return {"deliveries": deliveries.DELIVERIES.mine(request)}

    @app.get("/api/deliveries/{delivery_id}", tags=["Downloads"])
    def get_delivery(delivery_id: str, request: Request) -> dict[str, Any]:
        return {"delivery": deliveries.DELIVERIES.view(deliveries.DELIVERIES.find(request, delivery_id))}

    @app.get("/api/deliveries/{delivery_id}/file", tags=["Downloads"])
    def delivery_file(delivery_id: str, request: Request) -> Response:
        return deliveries.DELIVERIES.file(request, delivery_id)

    @app.delete("/api/deliveries/{delivery_id}", tags=["Downloads"])
    def delete_delivery(delivery_id: str, request: Request) -> dict[str, Any]:
        return deliveries.DELIVERIES.forget(request, delivery_id)
