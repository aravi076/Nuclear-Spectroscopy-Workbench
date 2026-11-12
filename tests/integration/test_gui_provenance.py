"""Regions and decisions made in the interface survive saving and reopening a project."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from nsw.ui.main_window import MainWindow
from tests.integration.test_gui_quantification import app, window  # noqa: F401

pytestmark = pytest.mark.gui


def test_regions_and_decisions_round_trip(window: MainWindow, tmp_path: Path) -> None:  # noqa: F811
    entry = window.state.synthetic_entries()[0]
    window.explorer.select_keys([f"syn:{entry.key}"], tab=2)
    controller = window.explorer.roi_controller
    assert controller.spectrum is not None and controller.spectrum.data_sha256 == entry.key
    roi = controller.create(100, 160)
    assert roi is not None
    window.state.record_assignment(
        entry.key, {"nuclide": "SYN-X", "line_kev": 650.0, "accepted": False}
    )
    assert window.new_project(tmp_path / "session", "round trip")
    report = window.save_session()
    assert report is not None and report.decisions >= 2
    path = window.project.path
    window.state.remove_roi(entry.key, roi.roi_id)
    assert window.open_project(path)
    restored = window.state.rois(entry.key)
    assert [(r.roi_id, r.first_channel, r.last_channel) for r in restored] == [
        (roi.roi_id, 100, 160)
    ]
    assert window.state.assignments(entry.key)[-1]["accepted"] is False
    assert "regions restored" in window.project_page.status_label.text()
