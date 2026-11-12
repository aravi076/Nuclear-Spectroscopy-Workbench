"""Problems found in the first hands-on test with a measured Co-60 spectrum (2026-11-12).

1. The peak search reported candidates at the lower-level discriminator edge.
2. Fixed centroids were printed with an uncertainty of 0.000.
3. (state.store_fit, tested in tests/integration/test_gui_state_fits.py)
4. Reports showed the full folder of the measured file.
5. Long labels were cut off in the PDF.
6. 32-bit vendor settings were printed with 16 digits.
"""

from __future__ import annotations

import numpy as np
import pytest

from nsw.analysis.fitting import FitSettings, fit_peaks
from nsw.analysis.peaks import PeakSearchSettings, find_peak_candidates, lower_cutoff_index
from nsw.reporting.document import KEY_WIDTH, Section, _lines
from nsw.services import describe
from nsw.services import report as rs
from nsw.services.peaks import peak_table
from tests.conftest import make_raw

SETTINGS = PeakSearchSettings(fwhm_channels=(4.5, 9.0), threshold=4.0)


def discriminator_spectrum(seed: int = 3, with_peak: bool = True) -> np.ndarray:  # type: ignore[type-arg]
    """Empty below channel 9, then a slowly falling continuum (as measured spectra are
    below a lower-level discriminator), optionally with one Gaussian peak at 300."""
    rng = np.random.default_rng(seed)
    x = np.arange(600, dtype=float)
    mean = 150.0 * np.exp(-x / 400.0)
    if with_peak:
        mean += 2000.0 / (2.0 * np.sqrt(2.0 * np.pi)) * np.exp(-0.5 * ((x - 300.0) / 2.0) ** 2)
    mean[:8] = 0.0
    return rng.poisson(mean)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ([0, 0, 0, 40, 150, 160, 140], 3),
        ([0, 0, 0, 1, 0, 0, 1, 4, 591, 2605], 6),  # isolated counts do not end the edge
        ([1, 0, 0, 0, 0, 0, 0, 1, 21, 57, 46], 7),  # a stray count in channel 1
        ([5, 6, 7, 8], 0),  # starts with counts: no cut-off
        ([0, 0, 1, 0, 0, 0], 0),  # never three occupied channels: no cut-off
    ],
)
def test_lower_cutoff_index(counts: list[int], expected: int) -> None:
    assert lower_cutoff_index(np.asarray(counts)) == expected


def test_a_lone_peak_on_empty_channels_is_not_a_discriminator_edge() -> None:
    peak_only = np.zeros(200, dtype=int)
    peak_only[50:60] = [3, 20, 80, 160, 200, 160, 80, 20, 3, 1]
    assert lower_cutoff_index(peak_only) == 50  # without the continuum check
    assert lower_cutoff_index(peak_only, sustain=72) == 0
    result = find_peak_candidates(make_raw(peak_only), SETTINGS)
    assert [round(c.channel) for c in result.candidates] == [55]


def test_no_candidates_at_the_discriminator_edge_but_real_peaks_found() -> None:
    for seed in range(5):
        spectrum = make_raw(discriminator_spectrum(seed))
        result = find_peak_candidates(spectrum, SETTINGS)
        channels = [c.channel for c in result.candidates]
        assert all(c > 30 for c in channels), (seed, channels)
        assert any(abs(c - 301.0) < 1.0 for c in channels), (seed, channels)  # 1-based
        assert any("lower-level discriminator" in w for w in result.warnings)


def test_spectrum_without_empty_start_is_searched_as_before() -> None:
    counts = discriminator_spectrum(1)
    counts[:8] = 150
    result = find_peak_candidates(make_raw(counts), SETTINGS)
    assert not any("discriminator" in w for w in result.warnings)


def test_fixed_centroid_and_width_are_labelled_fixed() -> None:
    spectrum = make_raw(discriminator_spectrum(2))
    free = fit_peaks(spectrum, 280, 320, [301.0], 4.7)
    held = fit_peaks(
        spectrum, 280, 320, [301.0], 4.7, FitSettings(fixed_centroids=True, fixed_width=True)
    )
    headers, rows = peak_table([free, held])
    i_c = headers.index("Centroid (ch)") + 1
    i_w = headers.index("FWHM (ch)") + 1
    assert rows[0][i_c] != "fixed" and rows[0][i_w] != "fixed"
    assert rows[1][i_c] == "fixed" and rows[1][i_w] == "fixed"


def test_report_names_the_file_but_not_its_folder() -> None:
    spectrum = make_raw(discriminator_spectrum(1))
    assert ("Path", spectrum.source.path) in describe.file_rows(spectrum)  # type: ignore[union-attr]
    report = rs.build_report(spectrum)
    rows = dict(next(s for s in report.sections if s.title == "File and data identity").rows)
    assert "Path" not in rows
    assert rows["Original file name"] == spectrum.source.filename  # type: ignore[union-attr]
    assert rows["File SHA-256"]


def test_long_labels_are_not_cut_off() -> None:
    long = "Vendor calibration in file (NOT VERIFIED, not applied)"
    assert len(long) > KEY_WIDTH
    lines = [t for _, t in _lines(Section("Energy calibration", [("Status", "x"), (long, "1, 2")]))]
    text = "\n".join(lines)
    assert long in text and "1, 2" in text


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("3300.366455078125", "3300.366"),
        ("8.800000190734863", "8.8"),
        ("0.10000000149011612", "0.1"),
        ("8192", "8192"),
        ("100", "100"),
        ("3300.37", "3300.37"),
        ("1.0.0.11564", "1.0.0.11564"),
        ("HPGe", "HPGe"),
    ],
)
def test_vendor_float_settings_are_shown_at_their_precision(raw: str, shown: str) -> None:
    assert describe.tidy_number(raw) == shown
