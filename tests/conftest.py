"""Every test runs on throwaway storage, never on the developer's real settings.

The project's .env (a developer's real storage database, secrets key, tokens) is not read during tests, and
storage settings inherited from the shell are removed. A test that needs a database sets it itself
(tests/test_system_db.py uses MONGO_TEST_URL, PG_TEST_URL, … which point at test servers only)."""
import os

os.environ["REPORTING_SKIP_ENV_FILE"] = "1"
for _name in ("REPORTING_STORAGE", "REPORTING_STORAGE_URL", "REPORTING_SECRETS_KEY", "REPORTING_DATA_DIR",
              "REPORTING_ORG_ID", "REPORTING_ENABLED_AREAS", "REPORTING_ACCESS_SECRET", "REPORTING_DESIGNER_TOKEN"):
    os.environ.pop(_name, None)
