"""Automatic peak search and candidate quality classes."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nsw.core.errors import ProcessingError
from nsw.services import candidates as cand
from tests.conftest import make_raw


def spectrum(peaks: list[tuple[float, float, float]], continuum: float = 50.0, n: int = 400):
    x = np.arange(1, n + 1, dtype=np.float64)
    y = np.full(n, continuum)
    for mu, fwhm, area in peaks:
        s = fwhm / 2.3548200450309493
        y += area * np.exp(-0.5 * ((x - mu) / s) ** 2) / (s * math.sqrt(2 * math.pi))
    return make_raw(np.rint(y).astype(int))


def by_channel(classified, channel: float):  # type: ignore[no-untyped-def]
    return min(classified, key=lambda cc: abs(cc.candidate.channel - channel))


def test_classes_from_significance_and_width() -> None:
    s = spectrum(
        [
            (80.0, 4.0, 20000.0),  # strong
            (160.0, 4.0, 15000.0),  # strong (gives the median width)
            (240.0, 4.0, 200.0),  # weak (significance about 6.5)
            (330.0, 16.0, 60000.0),  # very broad: not photopeak-like
        ]
    )
    found = cand.auto_search(s)
    classified = cand.classify(found)
    assert by_channel(classified, 80.0).quality is cand.CandidateQuality.STRONG
    assert by_channel(classified, 240.0).quality in (
        cand.CandidateQuality.WEAK,
        cand.CandidateQuality.DOUBTFUL,
    )
    broad = by_channel(classified, 330.0)
    assert broad.quality is cand.CandidateQuality.NOT_PHOTOPEAK
    assert "Compton edge" in broad.reasons[0] and "median of strong peaks" in broad.reasons[0]


def test_resolution_model_widths_are_used_when_given() -> None:
    s = spectrum([(100.0, 4.0, 20000.0), (250.0, 4.0, 20000.0)])
    found = cand.auto_search(s, expected_fwhm=lambda ch: 4.0)
    assert found.settings.fwhm_channels == (4.0, 8.0)
    classified = cand.classify(found, expected_fwhm=lambda ch: 12.0)  # model says 12
    assert all(cc.quality is cand.CandidateQuality.DOUBTFUL for cc in classified)
    assert all("resolution model" in cc.reasons[0] for cc in classified)


def test_exclusion_and_usable_search() -> None:
    s = spectrum([(100.0, 4.0, 20000.0), (250.0, 4.0, 20000.0), (330.0, 16.0, 60000.0)])
    found = cand.auto_search(s)
    first = by_channel(cand.classify(found), 100.0).candidate.channel
    classified = cand.classify(found, excluded=[first])
    assert by_channel(classified, 100.0).excluded
    kept = cand.usable(found, classified)
    channels = [c.channel for c in kept.candidates]
    assert all(abs(c - 100.0) > 1 for c in channels)  # excluded
    assert all(abs(c - 330.0) > 3 for c in channels)  # not photopeak-like
    assert any(abs(c - 250.0) < 1 for c in channels)
    text = cand.summary(classified)
    assert "1 excluded" in text and "not nuclides" in text


def test_search_region_and_threshold() -> None:
    s = spectrum([(100.0, 4.0, 20000.0), (250.0, 4.0, 20000.0)])
    local = cand.auto_search(s, first_channel=200, last_channel=300)
    assert [round(c.channel) for c in local.candidates] == [250]
    with pytest.raises(ProcessingError):
        cand.auto_search(s, threshold=0.0)
    assert cand.summary(()) == "No peak candidates."
