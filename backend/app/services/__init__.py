"""Service functions behind the routers. Each takes the service context `ctx` as its first argument.

bind_services(ctx) puts every exported function on ctx with ctx already filled in, so callers write
`ctx.require_admin(request)` (or unpack `require_admin = ctx.require_admin`)."""
from __future__ import annotations

from functools import partial, update_wrapper
from types import SimpleNamespace

from backend.app.services import access, accounts, connections, guards, history, records, repository, runs, versions, workspace

MODULES = (connections, guards, runs, repository, workspace, history, accounts, versions, access, records)


def bind_services(ctx: SimpleNamespace) -> None:
    for module in MODULES:
        for name in module.__all__:
            function = getattr(module, name)
            setattr(ctx, name, update_wrapper(partial(function, ctx), function))
