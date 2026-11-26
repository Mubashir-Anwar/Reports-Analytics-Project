"""Docker / compose helper files, and the fallback font for Urdu text."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _make_env():
    spec = importlib.util.spec_from_file_location("make_env", ROOT / "deploy" / "make_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_make_env_adds_what_is_missing_and_keeps_what_is_there():
    tool = _make_env()
    text, added = tool.merge("", "https://reports.example.com/")
    assert set(tool.NEEDED) <= set(added) and "REPORTING_PUBLIC_URL=https://reports.example.com\n" in text
    values = re.findall(r"^[A-Z_]+=([0-9a-f]{64})$", text, re.M)
    assert len(values) == len(tool.NEEDED) == len(set(values))
    assert tool.merge(text) == (text, [])                                 # a second run changes nothing
    mine, added = tool.merge("REPORTING_SECRETS_KEY=mine\n")
    assert "REPORTING_SECRETS_KEY=mine" in mine and "REPORTING_SECRETS_KEY" not in added
    with pytest.raises(ValueError, match="empty"):
        tool.merge("STORAGE_DB_PASSWORD=\n")


def test_every_requirement_has_a_tested_version():
    names = {re.split(r"[\[<>=]", line)[0].strip().lower() for line in (ROOT / "requirements.txt").read_text().splitlines() if line.strip()}
    pinned = {line.split("==")[0] for line in (ROOT / "constraints.txt").read_text().splitlines() if "==" in line}
    assert names <= pinned


def test_urdu_without_an_own_font_uses_a_font_of_the_machine(monkeypatch):
    from backend.app import fonts
    monkeypatch.setattr(fonts, "list_fonts", lambda: [])
    monkeypatch.setattr(fonts, "_SYSTEM", {})
    family = fonts.right_to_left_family()
    if family is None:
        pytest.skip("no font with Urdu letters on this machine")
    from reportlab.pdfbase import pdfmetrics
    regular, bold, *_ = fonts.pdf_family(family)
    for name in (regular, bold):
        assert 0x0679 in pdfmetrics.getFont(name).face.charToGlyph


def test_a_font_path_that_is_not_a_font_is_skipped(monkeypatch, tmp_path):
    from backend.app import fonts
    broken = tmp_path / "x.ttf"
    broken.write_bytes(b"no font")
    monkeypatch.setattr(fonts, "list_fonts", lambda: [])
    monkeypatch.setattr(fonts, "_SYSTEM", {})
    monkeypatch.setattr(fonts, "_SYSTEM_FILES", ())
    monkeypatch.setenv("REPORT_RTL_FONT_PATH", str(broken))
    assert fonts.right_to_left_family() is None


def test_no_env_file_is_committed_and_gitignore_covers_it():
    """.env holds the secrets of one installation: never in git (setup makes a new one on every computer)."""
    import shutil
    import subprocess
    root = Path(__file__).resolve().parents[1]
    git = shutil.which("git")
    if not git or not (root / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = subprocess.run([git, "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
    assert [name for name in tracked if Path(name).name == ".env" or Path(name).name.startswith(".env.")] == []
    for name in (".env", "deploy/.env", ".env.local"):
        assert subprocess.run([git, "check-ignore", "-q", name], cwd=root, check=False).returncode == 0, name
