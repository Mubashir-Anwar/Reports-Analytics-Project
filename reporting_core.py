"""Compatibility module.

The service now lives in the `backend.app` package (see ARCHITECTURE.md §3). This module re-exports every
name it used to define, so older imports such as `import reporting_core as core` keep working. New code
should import from the package modules directly. To replace a function in tests, patch it in the module
that defines it, e.g. `monkeypatch.setattr(core.mongodb, "MongoClient", Fake)`.
"""
import importlib as _importlib

_MODULES = (
    "backend.app.constants", "backend.app.logs", "backend.app.config",
    "backend.app.storage.files", "backend.app.storage.audit",
    "backend.app.security.access", "backend.app.security.secrets", "backend.app.security.users",
    "backend.app.connectors.errors", "backend.app.connectors.mongo_fallback", "backend.app.connectors.mongodb",
    "backend.app.connectors.profiles", "backend.app.queries.mongo_query", "backend.app.definitions",
    "backend.app.rendering.outputs", "backend.app.mrd", "backend.app.factory",
)
for _name in _MODULES:
    globals().update({key: value for key, value in vars(_importlib.import_module(_name)).items() if not key.startswith("__")})

from backend.app.connectors import mongodb  # noqa: E402  (module handles for monkeypatching)
from backend.app.storage import audit  # noqa: E402
from backend.app.factory import make_app  # noqa: E402,F401
