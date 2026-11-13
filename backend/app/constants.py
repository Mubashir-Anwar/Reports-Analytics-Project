"""Fixed values shared by the whole service (patterns, roles, formats, cookie names)."""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # the repository root (ui/, data/, reports/, .env)

# Areas that can be switched off per deployment (REPORTING_ENABLED_AREAS). /reports, /generate and /health are always on.
ALL_AREAS = ("runner", "admin", "designer", "server", "docs")
ROLE_TOKEN_MIN_LENGTH = 24
# /server login: users live in <data dir>/users.json (PBKDF2 hashes), sessions are signed cookies.
USER_ROLES = ("viewer", "designer", "admin")  # lowest to highest
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
MIN_PASSWORD_LENGTH = 8
PASSWORD_ITERATIONS = 240_000
SESSION_COOKIE = "rs_session"
BOOTSTRAP_USERNAME = "admin"
WORKSPACE_META = "_workspace"  # designer-only bookkeeping inside a workspace draft; never published

SERVICE_VERSION = "0.3.0"
OUTPUT_FORMATS = {"csv", "xlsx", "pdf"}
QUERY_LANGUAGES = {"aggregation-json", "mongosh", "javascript", "python", "sql"}
# Data adapter kinds: MongoDB, and SQL databases through backend/app/connectors/sql.py
ADAPTER_TYPES = {"mongodb", "postgresql", "mysql", "sqlserver", "oracle"}
METADATA_KEYS = {"GeneratedBy", "GeneratedByCode", "Folder", "Generate"}
# Signed /generate links carry these two query parameters; they are never report parameters.
SIGNED_LINK_KEYS = ("Expires", "Signature")
MIN_ACCESS_SECRET_LENGTH = 32
# Any of these means the request passed through a proxy, so "loopback client" proves nothing.
BOOTSTRAP_PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "via")
REPORT_PATH = re.compile(r"^([A-Za-z0-9_-]+)\.(csv|xlsx|pdf)$", re.IGNORECASE)
COLLECTION_TEMPLATE = re.compile(r"^(?:[A-Za-z0-9_.\-]|\$P\{[A-Za-z_][A-Za-z0-9_]*\}){1,160}$")
COLLECTION_PARAM_VALUE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
REPORT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
CONNECTION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ENV_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
