"""The packaged-application self-test runs from the source tree as well."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nsw.ui import selftest
from nsw.ui.app import create_application

pytestmark = pytest.mark.gui


def test_self_test_passes_every_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status = selftest.run(create_application([]), tmp_path)
    out = capsys.readouterr().out
    assert status == 0, out
    assert "self-test: 11 of 11 steps passed" in out
    assert (tmp_path / "selftest-report.pdf").exists()
    assert (tmp_path / "selftest.nswproj").exists()
