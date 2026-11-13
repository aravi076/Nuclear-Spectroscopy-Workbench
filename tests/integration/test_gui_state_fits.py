"""A new fit replaces earlier fits of overlapping regions (found in hands-on test 1:
the same Co-60 peak appeared twice in the report)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nsw.analysis.fitting import FitSettings, fit_peaks
from nsw.ui.state import AppState
from tests.conftest import make_raw
from tests.unit.test_report_findings_test1 import discriminator_spectrum

pytestmark = pytest.mark.gui
FIXED = FitSettings(fixed_centroids=True)  # no real peak there: hold the position


def test_overlapping_fit_replaces_earlier_and_separate_regions_stay(qtbot) -> None:  # type: ignore[no-untyped-def]
    spectrum = make_raw(discriminator_spectrum(4))
    key = spectrum.data_sha256
    state = AppState()
    wide = fit_peaks(spectrum, 280, 320, [301.0], 4.7)
    narrow = fit_peaks(spectrum, 292, 310, [301.0], 4.7)
    elsewhere = fit_peaks(spectrum, 100, 140, [120.0], 4.7, FIXED)
    # a neighbouring group whose margin overlaps the first region, without sharing a peak
    neighbour = fit_peaks(spectrum, 130, 170, [150.0], 4.7, FIXED)
    state.store_fit(key, wide)
    state.store_fit(key, elsewhere)
    state.store_fit(key, neighbour)
    state.store_fit(key, narrow)
    regions = [(r.first_channel, r.last_channel) for r in state.fits(key)]
    assert regions == [(100, 140), (130, 170), (292, 310)]
