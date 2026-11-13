"""Compatibility alias: this module moved to `backend.app.expressions.jexpr` (see ARCHITECTURE.md §3).
`import jexpr` gives that same module object, so attributes and monkeypatches behave as before."""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("backend.app.expressions.jexpr")
