"""scripts/release/scan_public_copy.py finds planted private data, including in Git history.

All "secrets" here are fake and assembled at run time, so this file itself does not
match the patterns it tests.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release" / "scan_public_copy.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scan_public_copy", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up
    spec.loader.exec_module(module)
    return module


scanner = _load()

FAKE_AWS = "AKIA" + "QWERTYUIOPASDFGH"
FAKE_GH = "ghp" + "_" + "a1B2c3D4" * 5
FAKE_PATH = "C:" + "\\Users\\" + "jdoe" + "\\Desktop\\spectra"
FAKE_MAIL = "jane.doe" + "@" + "university.example.edu"
PRIVATE_WORD = "Zyx" + "wvutSerial"


def _write(folder: Path, name: str, text: str) -> None:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _patterns(tmp_path: Path) -> Path:
    p = tmp_path / "patterns.txt"
    p.write_text("# private identifiers\n" + PRIVATE_WORD.lower() + "\n", encoding="utf-8")
    return p


def _run(folder: Path, patterns: Path, allow: Path | None = None) -> int:
    args = [str(folder), "--patterns", str(patterns)]
    args += ["--allow", str(allow if allow else folder / "none.txt")]
    return int(scanner.main(args))


def test_clean_folder_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    _write(repo, "README.md", "Contact: 1+someone@users.noreply.github.com\nC:\\Users\\Public\n")
    assert _run(repo, _patterns(tmp_path)) == 0
    assert "0 finding(s)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        (f"key = {FAKE_AWS}", "aws-key"),
        (f"token {FAKE_GH}", "github-token"),
        (f"data in {FAKE_PATH}", "user-profile-path"),
        (f"write to {FAKE_MAIL}", "email"),
        ("password = '" + "hunter2" * 2 + "'", "password-assignment"),
        ("-----BEGIN RSA " + "PRIVATE KEY-----", "private-key"),
    ],
)
def test_generic_patterns_are_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, pattern: str
) -> None:
    repo = tmp_path / "repo"
    _write(repo, "docs/notes.md", f"line one\n{text}\n")
    assert _run(repo, _patterns(tmp_path)) == 1
    assert f"{pattern}: docs/notes.md:2" in capsys.readouterr().out


def test_private_pattern_is_found_but_not_echoed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    _write(repo, "a.txt", f"detector {PRIVATE_WORD} 12\n")
    assert _run(repo, _patterns(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "private-1: a.txt:1" in out
    assert PRIVATE_WORD not in out


def test_binary_files_are_searched(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "shot.png").write_bytes(b"\x89PNG\x00\x01" + FAKE_PATH.encode() + b"\x00\xff")
    assert _run(repo, _patterns(tmp_path)) == 1


def test_allow_list_suppresses_a_reviewed_finding(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo, "CITATION.cff", f"email: {FAKE_MAIL}\n")
    allow = tmp_path / "allow.txt"
    allow.write_text("email CITATION.cff  # reviewed: public contact\n", encoding="utf-8")
    assert _run(repo, _patterns(tmp_path), allow) == 0


def test_missing_pattern_file_is_a_configuration_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _run(repo, tmp_path / "absent.txt") == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_secret_removed_from_history_is_still_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603 - fixed argument list in a temporary repository
            ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "1+test@users.noreply.github.com")
    git("config", "commit.gpgsign", "false")
    _write(repo, "config.txt", f"key = {FAKE_AWS}\n")
    git("add", ".")
    git("commit", "-q", "-m", "add config")
    (repo / "config.txt").write_text("key = <removed>\n", encoding="utf-8")
    git("commit", "-q", "-am", f"remove key; ask {FAKE_MAIL}")
    assert _run(repo, _patterns(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "aws-key: git:" in out and "config.txt" in out
    assert "email: git:commit" in out
    assert "aws-key: config.txt" not in out  # not in the working tree any more
