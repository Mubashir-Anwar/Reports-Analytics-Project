"""tools/harden_env.py adds only the missing checklist settings and never changes a value that is there."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import harden_env  # noqa: E402


def test_adds_only_what_is_missing(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("# mine\nREPORTING_SESSION_SECRET=keep-this\nREPORTING_SECRETS_KEY=\nOTHER=1\n", encoding="utf-8")
    assert harden_env.main(["--file", str(env), "--https", "--test-mailbox"]) == 0
    text = env.read_text(encoding="utf-8")
    names = harden_env.read_names(text)
    assert names["REPORTING_SESSION_SECRET"] == "keep-this" and names["OTHER"] == "1" and "# mine" in text
    assert len(names["REPORTING_SECRETS_KEY"]) >= 32 and text.count("REPORTING_SECRETS_KEY=") == 1
    assert names["REPORTING_ENABLED_AREAS"] == "runner,admin,designer,server,analytics"
    assert names["REPORTING_SECURE_COOKIES"] == "true" and names["REPORTING_HSTS"] == "true"
    assert names["SMTP_HOST"] == "127.0.0.1" and names["SMTP_FROM"]
    assert names["REPORTING_SECRETS_KEY"] not in capsys.readouterr().out       # secrets are never printed
    assert list(tmp_path.glob(".env.before-harden-*"))                          # the old file is kept
    assert harden_env.main(["--file", str(env), "--https", "--test-mailbox"]) == 0
    assert env.read_text(encoding="utf-8") == text                              # a second run changes nothing
    assert "nothing is missing" in capsys.readouterr().out


def test_check_changes_nothing_and_respects_the_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setenv("REPORTING_SECRETS_KEY", "from-the-service-settings-xxxxxxxxxxxxxxxx")
    assert harden_env.main(["--file", str(env), "--check"]) == 1 and not env.exists()
    assert harden_env.main(["--file", str(env)]) == 0
    assert "REPORTING_SECRETS_KEY" not in harden_env.read_names(env.read_text(encoding="utf-8"))


def test_worker_threads_follow_the_setting(monkeypatch):
    import anyio
    import anyio.to_thread

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backend.app.context import set_worker_threads

    async def check(value):
        monkeypatch.setenv("REPORTING_WORKER_THREADS", value)
        return set_worker_threads(), anyio.to_thread.current_default_thread_limiter().total_tokens

    assert anyio.run(check, "300") == (300, 300)
    assert anyio.run(check, "5") == (40, 40)          # never below AnyIO's own default
    assert anyio.run(check, "lots") == (200, 200)
