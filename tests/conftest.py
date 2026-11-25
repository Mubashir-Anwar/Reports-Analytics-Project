"""Every test runs on throwaway storage, never on the developer's real settings.

The project's .env (a developer's real storage database, secrets key, tokens) is not read during tests, and
storage settings inherited from the shell are removed. A test that needs a database sets it itself
(tests/test_system_db.py uses MONGO_TEST_URL, PG_TEST_URL, … which point at test servers only)."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["REPORTING_SKIP_ENV_FILE"] = "1"
for _name in ("REPORTING_STORAGE", "REPORTING_STORAGE_URL", "REPORTING_SECRETS_KEY", "REPORTING_DATA_DIR",
              "REPORTING_ORG_ID", "REPORTING_ENABLED_AREAS", "REPORTING_ACCESS_SECRET", "REPORTING_DESIGNER_TOKEN"):
    os.environ.pop(_name, None)

# A test that names no data folder of its own gets a temporary one: this project's data/ may belong to a running server
# (its request log, records, adapters), and its adapters must not be copied into the tests either.
_ROOT = Path(tempfile.mkdtemp(prefix="reporting-tests-")).resolve()
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)
os.environ["REPORTING_DATA_DIR"] = str(_ROOT / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.app.storage import files as _files  # noqa: E402

_files.PROJECT_ROOT = _ROOT
