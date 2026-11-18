"""Every public name of the service's main modules in one namespace, for the tests (`import service_names as core`).

New tests may import from the package modules directly. To replace a function, patch it in the module that defines it,
e.g. `monkeypatch.setattr(core.mongodb, "MongoClient", Fake)`."""
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
