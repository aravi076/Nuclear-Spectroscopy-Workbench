"""Workflow panel and beginner / advanced modes in the main window."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nsw.ui.main_window import PAGE_CALIBRATION, MainWindow
from nsw.ui.workflow_panel import ADVANCED, BEGINNER
from tests.integration.test_gui_quantification import app, window  # noqa: F401

pytestmark = pytest.mark.gui


def test_modes_hide_and_show_advanced_controls(window: MainWindow) -> None:  # noqa: F811
    advanced = window.explorer.roi_panel.edge_box
    assert window.mode == BEGINNER and advanced.isHidden()
    window.workflow.set_mode(ADVANCED)
    assert window.mode == ADVANCED and not advanced.isHidden()
    # state is untouched by switching
    entry = window.state.synthetic_entries()[0]
    window.explorer.select_keys([f"syn:{entry.key}"], tab=2)
    roi = window.explorer.roi_controller.create(100, 150)
    window.workflow.set_mode(BEGINNER)
    assert roi is not None and window.state.rois(entry.key) == [roi]


def test_panel_follows_the_selection_and_opens_pages(window: MainWindow) -> None:  # noqa: F811
    texts = window.workflow.stage_texts()
    assert len(texts) == 12 and "select a spectrum" in " ".join(texts)
    measured = window.state.experimental_entries()[0]
    window.explorer.select_keys([measured.key])
    texts = window.workflow.stage_texts()
    efficiency = next(t for t in texts if "Efficiency calculation" in t)
    assert efficiency.startswith("✗") and "certified source data" in efficiency
    item = window.workflow.list.item(6)  # Energy calibration
    window.workflow._activated(item)
    assert window.nav.currentRow() == PAGE_CALIBRATION
    assert window.workflow.next_label.text().startswith("Suggested next step")
