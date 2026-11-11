"""GUI workflow: create a project, save the session, reopen it in a new window."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from nsw.config import PathSettings, Settings
from nsw.simulation import presets
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.main_window import PAGE_PROJECT, MainWindow
from tests.fixtures import builders as b
from tests.integration.test_gui_peaks import BLANK, REAL, T_B, T_S, search
from tests.integration.test_gui_viewer import wait_until

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "experimental"
    root.mkdir()
    (root / "source 1000sec.csv").write_bytes(
        b.lynx_csv(REAL.tolist(), start=T_S, live=1000, real=1010.5)
    )
    (root / "blank 2000sec.csv").write_bytes(
        b.lynx_csv(BLANK.tolist(), start=T_B, live=2000, real=2001.5)
    )
    syn = tmp_path / "synthetic"
    specs = {s.synthetic_id: s for s in presets.hpge_scenarios()}
    write_synthetic(generate(specs["hpge-isolated-peaks-no-continuum"]), syn)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return root, syn, workspace


def make_window(app: QApplication, root: Path, workspace: Path) -> MainWindow:
    settings = Settings(paths=PathSettings(experimental_roots=(root,), workspace=workspace))
    w = MainWindow(settings)
    w.show()
    return w


@pytest.fixture
def errors(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    seen: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _p, title, text: seen.append(f"{title}: {text}")),
    )
    yield seen


def test_save_and_reopen_a_session(
    app: QApplication, folders: tuple[Path, Path, Path], errors: list[str]
) -> None:
    root, syn, workspace = folders
    w = make_window(app, root, workspace)
    w.load_experimental(root)
    wait_until(app, lambda: w.state.experimental is not None and not w.is_busy())
    w.load_synthetic(syn)
    wait_until(app, lambda: w.state.synthetic is not None and not w.is_busy())

    # nothing can be saved without a project, and none is written into the dataset
    assert w.save_session() is None and errors
    assert not w.new_project(root / "inside", "bad") and "read-only" in errors[-1]
    assert w.new_project(workspace / "session", "GUI test")
    assert w.act_save_project.isEnabled()

    blank = next(e for e in w.state.experimental_entries() if e.title.startswith("blank"))
    w.state.designate_background(blank.spectrum, "blank run")
    search(app, w, "hpge-isolated-peaks-no-continuum")
    page = w.peak_fit_page
    w.nav.setCurrentRow(4)
    syn_key = next(e.key for e in w.state.synthetic_entries())
    page.select(syn_key)
    page.fit_all_button.click()
    wait_until(app, lambda: not w.is_busy() and not page._busy)
    n_fits = len(w.state.fits(syn_key))
    assert n_fits >= 1

    report = w.save_session()
    assert report is not None and report.fits == n_fits and report.backgrounds == 1
    again = w.save_session()  # nothing new: no duplicated rows
    assert again is not None and again.fits == 0 and again.spectra == 0
    w.nav.setCurrentRow(PAGE_PROJECT)
    assert w.project_page.history.rows() and "GUI test" in w.project_page.info_label.text()
    assert w.backup_project() is not None
    path = w.project.path if w.project else None
    w.close()

    second = make_window(app, root, workspace)
    assert path is not None and second.open_project(path)
    wait_until(app, lambda: not second.is_busy())
    assert second.project_page.problems.toPlainText() == ""
    assert len(second.state.fits(syn_key)) == n_fits
    assert [e.title for e in second.state.background_entries()] == ["blank 2000sec.csv"]
    assert "fits reproduced" in second.project_page.status_label.text()
    second.close()


def test_opening_something_else_reports_an_error(
    app: QApplication, folders: tuple[Path, Path, Path], errors: list[str]
) -> None:
    root, _, workspace = folders
    w = make_window(app, root, workspace)
    bogus = workspace / "not-a-project.nswproj"
    bogus.write_text("x", encoding="utf-8")
    assert not w.open_project(bogus)
    assert errors and "not an NSW project" in errors[-1]
    assert w.project is None
    w.close()
