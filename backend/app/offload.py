"""Keep the server responsive while reports run.

Most API handlers are `async def` only so they can read the request body, and then do blocking work:
MongoDB/SQL queries, rendering a PDF/Excel file, storage reads and writes, password hashing. Run on the
event loop, one long Excel export froze every other request (pages, the designer, Postman calls) until
it finished, because the server has one worker.

`ThreadedRoute` keeps the handlers as they are and changes only how they are called: the body is read on
the event loop (Starlette caches it on the Request), then the handler runs to completion in a worker
thread with its own small event loop, where `await request.json()` / `form()` just read that cached body.
How many reports run at the same time is still limited by the RunGate (services/capacity.py).

Routes that make a report file can also "deliver later" (header X-Deliver: later, services/deliveries.py): the
request is answered at once with 202 and the handler runs in a background worker; the file is collected from
/api/deliveries/{id}/file. Leaving the page then no longer cancels a download.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
from typing import Any, Callable

from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from backend.app.services import deliveries


def _request_of(values: dict[str, Any]) -> Request | None:
    return next((value for value in values.values() if isinstance(value, Request)), None)


def threaded(handler: Callable[..., Any], deferrable: bool = False) -> Callable[..., Any]:
    @functools.wraps(handler)
    async def call(**values: Any) -> Any:
        request = _request_of(values)
        if request is not None:
            await request.body()                   # cached: the worker's json()/form() read this copy
        work = lambda: asyncio.run(handler(**values))  # noqa: E731
        if deferrable and request is not None and deliveries.wants_later(request):
            return await run_in_threadpool(deliveries.DELIVERIES.submit, request, work)
        return await run_in_threadpool(work)
    return call


def deferred_sync(handler: Callable[..., Any]) -> Callable[..., Any]:
    """A plain `def` endpoint (FastAPI already runs it in a thread) that can also deliver later."""
    @functools.wraps(handler)
    def call(**values: Any) -> Any:
        request = _request_of(values)
        if request is not None and deliveries.wants_later(request):
            return deliveries.DELIVERIES.submit(request, lambda: handler(**values))
        return handler(**values)
    return call


class ThreadedRoute(APIRoute):
    """An APIRoute whose `async def` endpoint runs in a worker thread instead of on the event loop."""

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, endpoint, **kwargs)
        # Swap only the callable FastAPI invokes: the signature and annotations were already read from
        # the real endpoint above (the routers use postponed annotations, so wrapping it earlier would
        # break that). FastAPI still awaits it because the original endpoint is a coroutine function.
        deferrable = path in deliveries.DEFERRABLE
        if self.dependant.call is not endpoint:
            return
        if inspect.iscoroutinefunction(endpoint):
            self.dependant.call = threaded(endpoint, deferrable)
        elif deferrable:
            self.dependant.call = deferred_sync(endpoint)
