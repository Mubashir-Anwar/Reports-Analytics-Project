"""Errors raised by the connectors."""
from __future__ import annotations


class MongoReadAccessError(RuntimeError):
    """The current MongoDB identity cannot read a collection needed by a report."""


class ConnectorQueryError(RuntimeError):
    """A data adapter could not connect or its query failed. The message is safe to show (no passwords)."""
