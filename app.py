"""MongoDB Reporting Service — entry point for `uvicorn app:app` (same as `uvicorn backend.app.main:app`)."""
from backend.app.main import app  # noqa: F401
