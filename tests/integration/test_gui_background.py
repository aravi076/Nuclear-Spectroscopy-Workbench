"""GUI workflow tests for the Background Subtraction page (offscreen)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from nsw.config import PathSettings, Settings
from nsw.core import SpectrumKind
from nsw.services.export import CALIBRATION_WARNING, read_exported_values
from nsw.simulation import presets
from nsw.simulation.generator import generate, write_synthetic
from nsw.ui.app import create_application
from nsw.ui.background_page import NO_BACKGROUND_HINT
from nsw.ui.main_window import PAGE_BACKGROUND, MainWindow
from tests.fixtures import builders as b
from tests.integration.test_gui_viewer import wait_until

pytestmark = pytest.mark.gui

G = [100 + (i * 7) % 50 for i in range(256)]
G_EARLY = [c // 2 for c in G]
BG1 = [(i * 11) % 40 + 1 for i in range(256)]
BG2 = [(i * 13) % 40 + 2 for i in range(256)]
T_G = "Tue Mar 10 09:15:27 GMT+0530 2026"
T_B1 = "Tue Mar 10 16:00:00 GMT+0530 2026"
T_B2 = "Tue Apr 28 09:00:00 GMT+0530 2026"


@pytest.fixture(scope="module")
def app() -> QApplication:
    return create_application([])


@pytest.fixture
def window(app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    root = tmp_path / "experimental"
    root.mkdir()
    (root / "sample 200sec.csv").write_bytes(b.lynx_csv(G, start=T_G, live=200, real=204.5))
    (root / "sample 100sec.csv").write_bytes(b.lynx_csv(G_EARLY, start=T_G, live=100, real=102.5))
    (root / "blank A.csv").write_bytes(b.lynx_csv(BG1, start=T_B1, live=400, real=400.5))
    (root / "blank B.csv").write_bytes(b.lynx_csv(BG2, start=T_B2, live=800, real=801.5))
    settings = Settings(paths=PathSettings(experimental_roots=(root,), workspace=tmp_path / "ws"))
    w = MainWindow(settings)
    w.show()
    w.load_experimental(root)
    wait_until(app, lambda: w.state.experimental is not None and not w.is_busy())
    w.nav.setCurrentRow(PAGE_BACKGROUND)
    yield w
    w.close()


def entries(w: MainWindow) -> dict[str, object]:
    return {e.title: e for e in w.state.experimental_entries()}


def test_page_is_disabled_until_a_background_is_designated(window: MainWindow) -> None:
    page = window.background_page
    assert window.nav.item(PAGE_BACKGROUND).text() == "Background Subtraction"
    assert page.background_box.count() == 0 and page.gross_box.count() == 4
    assert not page.subtract_button.isEnabled() and not page.export_button.isEnabled()
    assert page.messages.toPlainText() == NO_BACKGROUND_HINT


def test_subtraction_workflow(app: QApplication, window: MainWindow, tmp_path: Path) -> None:
    page = window.background_page
    e = entries(window)
    window.state.designate_background(e["blank A.csv"].spectrum)  # type: ignore[attr-defined]
    app.processEvents()
    assert page.background_box.count() == 1 and page.gross_box.count() == 3
    page.select(e["sample 200sec.csv"].key, e["blank A.csv"].key)  # type: ignore[attr-defined]
    assert page.subtract_button.isEnabled()
    assert "REFUSED" not in page.messages.toPlainText()
    assert page.result is None  # nothing is calculated until asked

    page.subtract_button.click()
    app.processEvents()
    r = page.result
    assert r is not None and r.alpha == 0.5  # 200 s / 400 s
    np.testing.assert_allclose(r.net.counts, np.array(G) - 0.5 * np.array(BG1))
    np.testing.assert_allclose(r.net.variance_array(), np.array(G) + 0.25 * np.array(BG1))
    rows = dict(page.result_table.rows())
    assert rows["Scaling factor α"].startswith("0.5 ")
    assert rows["Equation"] == "N_net = N_gross − α · N_background"
    assert "Assumption" in rows

    labels = [i.label for i in page.plot.items()]
    assert labels[0] == "gross" and labels[1].startswith("background × α") and "net" in labels[2]
    np.testing.assert_allclose(page.plot.displayed_y(1), 0.5 * np.array(BG1))  # scaled for view
    np.testing.assert_allclose(page.plot.displayed_y(2), r.net.counts)
    assert page.plot.banner.text() == CALIBRATION_WARNING
    assert page.plot.x_axis_label() == "Channel"

    out = window.export_csv(tmp_path / "net.csv", spectrum=page.net_spectrum())
    assert out is not None
    np.testing.assert_allclose(read_exported_values(out), r.net.counts)
    text = out.read_text(encoding="utf-8")
    assert "# kind: net" in text and "processing: background-subtraction" in text
    assert page.suggested_filename() == "sample 200sec_net.csv"

    # originals are untouched
    gross = e["sample 200sec.csv"].spectrum  # type: ignore[attr-defined]
    assert gross.kind is SpectrumKind.RAW and gross.verify_integrity()
    np.testing.assert_array_equal(gross.counts, G)


def test_changing_the_inputs_discards_the_previous_result(
    app: QApplication, window: MainWindow
) -> None:
    page = window.background_page
    e = entries(window)
    window.state.designate_background(e["blank A.csv"].spectrum)  # type: ignore[attr-defined]
    app.processEvents()
    page.select(e["sample 200sec.csv"].key, e["blank A.csv"].key)  # type: ignore[attr-defined]
    page.run()
    assert page.result is not None and page.export_button.isEnabled()
    page.gross_box.setCurrentIndex(page.gross_box.findData(e["sample 100sec.csv"].key))  # type: ignore[attr-defined]
    assert page.result is None and not page.export_button.isEnabled()
    assert page.result_table.rowCount() == 0


def test_backgrounds_are_compared_only_when_independent(
    app: QApplication, window: MainWindow
) -> None:
    page = window.background_page
    e = entries(window)
    window.state.designate_background(e["blank A.csv"].spectrum)  # type: ignore[attr-defined]
    assert "at least two" in page.comparison_table.rows()[0][1]
    window.state.designate_background(e["blank B.csv"].spectrum)  # type: ignore[attr-defined]
    app.processEvents()
    ((pair, text),) = page.comparison_table.rows()
    assert "blank A.csv" in pair and "blank B.csv" in pair
    assert "χ²" in text and "p =" in text
    window.state.remove_background(e["blank B.csv"].key)  # type: ignore[attr-defined]
    app.processEvents()
    assert page.background_box.count() == 1 and page.gross_box.count() == 3


def test_synthetic_background_cannot_be_applied_to_experimental_data(
    app: QApplication, window: MainWindow, tmp_path: Path
) -> None:
    syn = tmp_path / "synthetic"
    specs = {s.synthetic_id: s for s in presets.nai_scenarios()}
    src = generate(specs["nai-isolated-peaks"])
    bg = generate(specs["nai-background-only"])
    write_synthetic(src, syn)
    write_synthetic(bg, syn)
    window.load_synthetic(syn)
    wait_until(app, lambda: window.state.synthetic is not None and not window.is_busy())
    page = window.background_page
    e = entries(window)
    bg_key = bg.spectrum.data_sha256
    assert page.background_box.findData(bg_key) >= 0  # synthetic background role is listed
    assert "SYNTHETIC" in page.background_box.itemText(page.background_box.findData(bg_key))

    page.select(e["sample 200sec.csv"].key, bg_key)  # type: ignore[attr-defined]
    assert not page.subtract_button.isEnabled()
    text = page.messages.toPlainText()
    assert "REFUSED: cannot combine experimental data with synthetic data" in text
    page.run()
    assert page.result is None

    page.select(src.spectrum.data_sha256, bg_key)  # synthetic with synthetic is fine
    assert page.subtract_button.isEnabled()
    page.run()
    assert page.result is not None and page.result.alpha == pytest.approx(0.2)
    assert page.result.net.is_synthetic
    assert "SYNTHETIC DATA" in page.plot.banner.text()
