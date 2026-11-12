"""GUI workflow tests for the spectrum viewer, run on Qt's offscreen platform.

They drive the real window classes: loading through the background worker, selecting
spectra, reading what is displayed, exporting. Pixel-level rendering and manual mouse
interaction are not covered here.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from nsw.config import PathSettings, Settings
from nsw.core import MeasurementStatus, SpectrumKind
from nsw.services.export import CALIBRATION_WARNING, SYNTHETIC_NOTICE, read_exported_values
from nsw.simulation import presets
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.explorer import TAB_BACKGROUND, TAB_RAW, TAB_SYNTHETIC
from nsw.ui.main_window import PAGE_EXPLORER, MainWindow
from tests.fixtures import builders as b

pytestmark = pytest.mark.gui

A = [(i * 7) % 50 for i in range(256)]
A2 = [c * 2 for c in A]
OTHER = [(i * 11) % 40 + 1 for i in range(256)]
T0 = "Tue Mar 10 09:15:27 GMT+0530 2026"
T1 = "Tue Mar 10 16:00:00 GMT+0530 2026"


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


def wait_until(app: QApplication, condition: Callable[[], bool], timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise TimeoutError("GUI condition not reached")
        time.sleep(0.005)
    app.processEvents()


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "experimental"
    root.mkdir()
    (root / "run 100sec.csv").write_bytes(b.lynx_csv(A, start=T0, live=100, real=101.5))
    (root / "run 200sec.ava").write_bytes(
        b.lynx_ava(A2, start=T0, start_ms=None, live=200, real=203.5)
    )
    (root / "bg 100sec.csv").write_bytes(b.lynx_csv(OTHER, start=T1, live=100, real=100.25))
    (root / "notes.pdf").write_bytes(b"%PDF-1.7 ...")
    return root


@pytest.fixture
def window(app: QApplication, tmp_path: Path, dataset: Path) -> Iterator[MainWindow]:
    settings = Settings(
        paths=PathSettings(experimental_roots=(dataset,), workspace=tmp_path / "ws")
    )
    w = MainWindow(settings)
    w.show()
    yield w
    w.close()
    wait_until(app, lambda: not w.is_busy())


def load(app: QApplication, w: MainWindow, folder: Path) -> None:
    w.load_experimental(folder)
    wait_until(app, lambda: w.state.experimental is not None and not w.is_busy())


def keys(w: MainWindow) -> dict[str, str]:
    return {e.title: e.key for e in w.state.experimental_entries()}


# ---------------------------------------------------------------- launch and loading
def test_window_launches_with_empty_state(window: MainWindow) -> None:
    assert window.isVisible()
    assert "Nuclear Spectroscopy Workbench" in window.windowTitle()
    assert [window.nav.item(i).text() for i in range(2)] == [
        "Project Dashboard",
        "Spectrum Explorer",
    ]
    assert window.explorer.plot.banner.text() == "No spectrum selected."
    assert not window.act_csv.isEnabled()


def test_loading_runs_in_background_and_fills_dashboard(
    app: QApplication, window: MainWindow, dataset: Path
) -> None:
    window.load_experimental(dataset)
    assert window.is_busy()  # returned immediately; work continues on a pool thread
    wait_until(app, lambda: not window.is_busy())
    d = window.dashboard
    assert d.summary_value("spectrum files imported") == "3"
    assert d.summary_value("unsupported files") == "1"
    assert d.summary_value("independent measurements") == "2"
    assert d.summary_value("intermediate saves") == "1"
    finding_kinds = {d.findings.item(r, 0).text() for r in range(d.findings.rowCount())}
    assert {"unsupported file", "successive-saves"} <= finding_kinds
    assert str(dataset) in d.root_label.text()


def test_missing_folder_reports_error_without_crashing(
    app: QApplication, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    window.load_experimental(tmp_path / "does-not-exist")
    wait_until(app, lambda: not window.is_busy())
    assert shown and "NotADirectoryError" in shown[0]
    assert window.state.experimental is None


# ---------------------------------------------------------------- display
def test_selected_spectrum_is_displayed_by_channel(
    app: QApplication, window: MainWindow, dataset: Path
) -> None:
    load(app, window, dataset)
    window.nav.setCurrentRow(PAGE_EXPLORER)
    ex = window.explorer
    ex.select_keys([keys(window)["bg 100sec.csv"]])
    app.processEvents()
    assert [i.label for i in ex.plot.items()] == ["bg 100sec.csv"]
    np.testing.assert_array_equal(ex.plot.displayed_y(0), OTHER)  # counts shown unchanged
    assert ex.plot.x_axis_label() == "Channel"
    assert "keV" not in ex.plot.x_axis_label() and "keV" not in ex.plot.y_axis_label()
    assert ex.plot.banner.text() == CALIBRATION_WARNING
    assert window.act_csv.isEnabled()


def test_cursor_reads_channel_and_counts(
    app: QApplication, window: MainWindow, dataset: Path
) -> None:
    load(app, window, dataset)
    ex = window.explorer
    ex.select_keys([keys(window)["bg 100sec.csv"]])
    ex.plot.set_cursor_channel(3)
    text = ex.plot.readout.text()
    assert text.startswith("Channel 3")
    assert f"{OTHER[2]:,} ± {OTHER[2] ** 0.5:,.2f} counts" in text  # channel 3 = index 2
    ex.plot.set_cursor_channel(10_000)  # clamps to the last channel
    assert ex.plot.readout.text().startswith("Channel 256")


def test_metadata_panels(app: QApplication, window: MainWindow, dataset: Path) -> None:
    load(app, window, dataset)
    ex = window.explorer
    ex.select_keys([keys(window)["run 200sec.ava"]])
    m = dict(ex.measurement_table.rows())
    assert m["Live time"] == "200 s" and m["Real time"] == "203.5 s"
    assert m["Data origin"] == "EXPERIMENTAL"
    assert m["Energy calibration status"] == "UNVERIFIED"
    assert "verified energy calibration" in m["Missing metadata"]
    assert m["Vendor calibration in file (NOT VERIFIED, not applied)"] == "-1.5, 0.25, 0"
    det = dict(ex.detector_table.rows())
    assert det["Detector identifier"] == "HPGe@Lynx-II-12345"
    assert det["Source-detector geometry"] == "not recorded"
    f = dict(ex.file_table.rows())
    assert f["Original file name"] == "run 200sec.ava" and len(f["File SHA-256"]) == 64
    ex.select_keys([keys(window)["bg 100sec.csv"]])
    assert "not recorded in this file format" in dict(ex.detector_table.rows())["Hardware settings"]


def test_intermediate_save_is_shown_as_such(
    app: QApplication, window: MainWindow, dataset: Path
) -> None:
    load(app, window, dataset)
    ex = window.explorer
    by_title = {e.title: e for e in window.state.experimental_entries()}
    assert (
        by_title["run 100sec.csv"].spectrum.measurement_status
        is MeasurementStatus.INTERMEDIATE_SAVE
    )
    ex.select_keys([by_title["run 100sec.csv"].key])
    assert dict(ex.measurement_table.rows())["Measurement status"] == "intermediate-save"
    tree = ex.trees[TAB_RAW]
    parents = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    final = next(p for p in parents if p.text(0) == "run 200sec.ava")
    assert final.childCount() == 1 and final.child(0).text(0) == "run 100sec.csv"
    assert "final of 2 saves" in final.text(4)


def test_comparison_overlay_and_display_options(
    app: QApplication, window: MainWindow, dataset: Path
) -> None:
    load(app, window, dataset)
    ex = window.explorer
    k = keys(window)
    ex.select_keys([k["bg 100sec.csv"], k["run 200sec.ava"]])
    assert len(ex.plot.items()) == 2
    labels = [i.label for i in ex.plot.items()]
    i200 = labels.index("run 200sec.ava")
    ex.plot.rate_box.setChecked(True)
    np.testing.assert_allclose(ex.plot.displayed_y(i200), np.array(A2) / 200.0)
    assert "per live second" in ex.plot.y_axis_label()
    ex.plot.log_box.setChecked(True)
    assert "empty channels drawn at the axis floor" in ex.plot.y_axis_label()
    ex.plot.rate_box.setChecked(False)
    ex.plot.log_box.setChecked(False)
    np.testing.assert_array_equal(ex.plot.displayed_y(i200), A2)


def test_zoom_and_pan_change_the_view_not_the_data(
    app: QApplication, window: MainWindow, dataset: Path
) -> None:
    load(app, window, dataset)
    ex = window.explorer
    ex.select_keys([keys(window)["bg 100sec.csv"]])
    spectrum = ex.current_spectrum()
    assert spectrum is not None
    vb = ex.plot.plot.getPlotItem().vb
    assert vb.state["mouseEnabled"] == [True, True]
    vb.setRange(xRange=(50, 100), padding=0)
    app.processEvents()
    (x0, x1), _ = vb.viewRange()
    assert (round(x0), round(x1)) == (50, 100)
    vb.translateBy(x=20)  # pan
    app.processEvents()
    assert round(vb.viewRange()[0][0]) == 70
    vb.scaleBy(x=0.5)  # zoom in
    app.processEvents()
    assert vb.viewRange()[0][1] - vb.viewRange()[0][0] == pytest.approx(25.0)
    ex.plot.reset_view()
    app.processEvents()
    assert vb.viewRange()[0][0] < 5 and vb.viewRange()[0][1] > 250
    assert spectrum.verify_integrity()
    np.testing.assert_array_equal(spectrum.counts, OTHER)


# ---------------------------------------------------------------- background / synthetic views
def test_background_designation_is_an_explicit_analyst_action(
    app: QApplication, window: MainWindow, dataset: Path
) -> None:
    load(app, window, dataset)
    ex = window.explorer
    assert ex.trees[TAB_BACKGROUND].topLevelItemCount() == 0  # nothing inferred from names
    entries = {e.title: e for e in window.state.experimental_entries()}
    with pytest.raises(ValueError, match="independent"):
        window.state.designate_background(entries["run 100sec.csv"].spectrum)
    window.state.designate_background(entries["bg 100sec.csv"].spectrum)
    app.processEvents()
    assert ex.trees[TAB_BACKGROUND].topLevelItemCount() == 1
    ex.select_keys(["bg:" + entries["bg 100sec.csv"].key], tab=TAB_BACKGROUND)
    shown = ex.current_spectrum()
    assert shown is not None and shown.kind is SpectrumKind.BACKGROUND
    np.testing.assert_array_equal(shown.counts, OTHER)
    assert "designate-background" in dict(ex.history_table.rows())["Processing step 1"]
    assert entries["bg 100sec.csv"].spectrum.kind is SpectrumKind.RAW  # original untouched


def test_synthetic_view_is_separate_and_labelled(
    app: QApplication, window: MainWindow, tmp_path: Path, dataset: Path
) -> None:
    syn_dir = tmp_path / "synthetic"
    result = generate(presets.nai_scenarios()[0])
    write_synthetic(result, syn_dir / "nai")
    window.load_synthetic(syn_dir)
    wait_until(app, lambda: window.state.synthetic is not None and not window.is_busy())
    ex = window.explorer
    assert ex.trees[TAB_RAW].topLevelItemCount() == 0
    assert ex.trees[TAB_SYNTHETIC].topLevelItemCount() == 1
    ex.select_keys(["syn:" + result.spectrum.data_sha256], tab=TAB_SYNTHETIC)
    assert ex.plot.banner.text() == SYNTHETIC_NOTICE
    assert ex.plot.x_axis_label() == "Channel"
    assert dict(ex.measurement_table.rows())["Data origin"] == "SYNTHETIC"
    np.testing.assert_array_equal(ex.plot.displayed_y(0), result.spectrum.counts)


def test_folders_of_the_wrong_kind_are_refused(
    app: QApplication,
    window: MainWindow,
    tmp_path: Path,
    dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    syn_dir = tmp_path / "synthetic"
    write_synthetic(generate(presets.hpge_scenarios()[3]), syn_dir)
    window.load_experimental(syn_dir)
    wait_until(app, lambda: not window.is_busy())
    assert window.state.experimental is None and "synthetic spectra found" in shown[-1]
    window.load_synthetic(dataset)
    wait_until(app, lambda: not window.is_busy())
    assert window.state.synthetic is None and "non-synthetic" in shown[-1]


# ---------------------------------------------------------------- export
def test_csv_and_png_export(
    app: QApplication, window: MainWindow, tmp_path: Path, dataset: Path
) -> None:
    load(app, window, dataset)
    window.explorer.select_keys([keys(window)["bg 100sec.csv"]])
    out = tmp_path / "out"
    out.mkdir()
    csv_path = window.export_csv(out / "bg.csv")
    assert csv_path is not None
    np.testing.assert_array_equal(read_exported_values(csv_path), OTHER)
    text = csv_path.read_text(encoding="utf-8")
    assert CALIBRATION_WARNING in text and "channel,value,uncertainty" in text
    assert "keV" not in text.replace(CALIBRATION_WARNING, "")
    assert "source file: bg 100sec.csv" in text
    png_path = window.export_png(out / "plot.png")
    assert png_path is not None and png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert png_path.stat().st_size > 2000


def test_export_can_never_write_into_the_experimental_dataset(
    app: QApplication,
    window: MainWindow,
    tmp_path: Path,
    dataset: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    load(app, window, dataset)
    window.explorer.select_keys([keys(window)["bg 100sec.csv"]])
    before = sorted(p.name for p in dataset.iterdir())
    assert window.export_csv(dataset / "export.csv") is None
    assert window.export_png(dataset / "sub.png") is None
    assert window.export_csv(dataset / "bg 100sec.csv", overwrite=True) is None
    assert all("read-only experimental dataset" in s for s in shown) and len(shown) == 3
    assert sorted(p.name for p in dataset.iterdir()) == before
    existing = tmp_path / "exists.csv"
    existing.write_text("keep me", encoding="utf-8")
    assert window.export_csv(existing) is None  # no silent overwrite
    assert existing.read_text(encoding="utf-8") == "keep me"


# ---------------------------------------------------------------- real data
