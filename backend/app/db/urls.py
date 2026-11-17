"""Turn the storage URL people write into the form each driver needs (and hide its password in messages)."""
from __future__ import annotations

import re

# what people type -> SQLAlchemy driver URL prefix (the drivers are already in requirements.txt)
_SQL_SCHEMES = {
    "postgresql": "postgresql+psycopg", "postgres": "postgresql+psycopg",
    "mysql": "mysql+pymysql", "mariadb": "mysql+pymysql",
    "sqlserver": "mssql+pymssql", "mssql": "mssql+pymssql",
    "oracle": "oracle+oracledb",
    "sqlite": "sqlite",
}
SQL_KINDS = {"postgresql": "PostgreSQL", "mysql": "MySQL", "mssql": "SQL Server", "oracle": "Oracle", "sqlite": "SQLite"}


def kind_of(url: str) -> str:
    """files | sql | mongodb from a URL's scheme ('' when not recognised)."""
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme in ("mongodb", "mongodb+srv"):
        return "mongodb"
    if scheme.split("+", 1)[0] in _SQL_SCHEMES:
        return "sql"
    return ""


def sqlalchemy_url(url: str) -> str:
    scheme, rest = url.split(":", 1)
    base = scheme.lower().split("+", 1)[0]
    if base not in _SQL_SCHEMES:
        raise ValueError("Use a postgresql://, mysql://, sqlserver://, oracle:// or sqlite:/// URL")
    if "+" in scheme:            # the user named a driver already (e.g. mssql+pyodbc://)
        return url
    return f"{_SQL_SCHEMES[base]}:{rest}"


def dialect_label(url: str) -> str:
    base = sqlalchemy_url(url).split(":", 1)[0].split("+", 1)[0]
    return SQL_KINDS.get(base, base)


def redact(url: str) -> str:
    """postgresql://user:secret@host/db -> postgresql://user:***@host/db"""
    return re.sub(r"(//[^:/@]+:)[^@]*@", r"\1***@", url)
