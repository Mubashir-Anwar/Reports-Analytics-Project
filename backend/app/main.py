"""ASGI entry point: `uvicorn backend.app.main:app` (the root app.py imports this too)."""
from backend.app.factory import make_app

app = make_app()
