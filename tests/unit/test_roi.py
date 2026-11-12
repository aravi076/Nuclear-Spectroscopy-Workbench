"""Regions of interest: validation, proposals, net area, limits and fitting."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from nsw.analysis.fitting import FitSettings
from nsw.analysis.limits import LimitMethod, LimitSettings
from nsw.analysis.peaks import PeakSearchSettings, find_peak_candidates
from nsw.core.errors import ProcessingError
from nsw.services import roi as rs
from tests.conftest import make_raw


def gaussian_counts(
    n: int, peaks: list[tuple[float, float, float]], continuum: float
) -> np.ndarray:
    """Expected counts: flat continuum plus Gaussians (centre channel, FWHM, area)."""
    x = np.arange(1, n + 1, dtype=np.float64)
    y = np.full(n, continuum)
    for mu, fwhm, area in peaks:
        s = fwhm / 2.3548200450309493
        y += area * np.exp(-0.5 * ((x - mu) / s) ** 2) / (s * math.sqrt(2 * math.pi))
    return y


def test_make_roi_orders_edges_and_refuses_bad_ranges() -> None:
    s = make_raw(np.zeros(50, dtype=int))
    roi = rs.make_roi(s, 30, 10)
    assert (roi.first_channel, roi.last_channel, roi.n_channels) == (10, 30, 21)
    with pytest.raises(ProcessingError, match="not inside"):
        rs.make_roi(s, 40, 60)
    with pytest.raises(ProcessingError, match="at least"):
        rs.make_roi(s, 5, 6)
    with pytest.raises(ProcessingError, match="outside the region"):
        rs.make_roi(s, 5, 20, rs.RoiKind.SINGLE, (25.0,), (3.0,))
    other = make_raw(np.ones(50, dtype=int))
    assert "different spectrum" in " ".join(rs.validate_roi(roi, other))


def test_moving_keeps_identity_and_drops_outside_peaks() -> None:
    s = make_raw(np.zeros(100, dtype=int))
    roi = rs.make_roi(s, 10, 60, rs.RoiKind.MULTIPLET, (20.0, 50.0), (3.0, 3.0))
    moved = roi.moved_to(30, 70)
    assert moved.roi_id == roi.roi_id and moved.peak_channels == (50.0,)
    assert moved.as_record()["first_channel"] == 30


def test_net_area_is_exact_for_a_linear_continuum() -> None:
    n = 61
    x = np.arange(1, n + 1)
    continuum = 200 + 3 * x  # linear
    peak = np.zeros(n, dtype=int)
    peak[28:33] = [10, 60, 100, 60, 10]
    s = make_raw(continuum + peak)
    roi = rs.make_roi(s, 11, 51)
    a = rs.net_area(roi, s, edge_channels=4)
    assert a.net == pytest.approx(240.0, abs=1e-9)
    assert (a.inner_first, a.inner_last) == (15, 47)
    inner = continuum[14:47]
    assert a.background == pytest.approx(inner.sum(), rel=1e-12)
    # variance: gross Poisson + continuum estimate from 2 x 4 edge channels
    expected_bvar = (33 / 8) ** 2 * (continuum[10:14].sum() + continuum[47:51].sum())
    assert a.background_variance == pytest.approx(expected_bvar, rel=1e-12)
    gross_var = (continuum + peak)[14:47].sum()
    assert a.net_uncertainty == pytest.approx(math.sqrt(gross_var + expected_bvar), rel=1e-12)


def test_net_area_keeps_negative_values_and_needs_edges() -> None:
    counts = np.full(30, 100)
    counts[12:17] = 90
    s = make_raw(counts)
    a = rs.net_area(rs.make_roi(s, 5, 25), s)
    assert a.net == pytest.approx(-50.0)
    with pytest.raises(ProcessingError, match="continuum channels"):
        rs.net_area(rs.make_roi(s, 5, 10), s, edge_channels=3)


def test_net_area_coverage_monte_carlo() -> None:
    """The stated uncertainty covers the true net area in ~68 % of Poisson trials."""
    rng = np.random.default_rng(7)
    mean = gaussian_counts(80, [(40.0, 4.0, 800.0)], 150.0)
    true_net = None
    pulls = []
    for _ in range(3000):
        s = make_raw(rng.poisson(mean))
        roi = rs.make_roi(s, 25, 55)
        a = rs.net_area(roi, s)
        if true_net is None:
            inner = mean[a.inner_first - 1 : a.inner_last]
            true_net = float(inner.sum() - 150.0 * inner.size)
        pulls.append((a.net - true_net) / a.net_uncertainty)
    p = np.asarray(pulls)
    assert abs(p.mean()) < 5 / math.sqrt(p.size)
    assert abs(p.std() - 1.0) < 0.05


def test_limits_follow_the_selected_method() -> None:
    s = make_raw(np.full(41, 400))
    a = rs.net_area(rs.make_roi(s, 1, 41), s, edge_channels=5)
    iso = rs.limits(a, LimitSettings(), w=0.01, w_rel_uncertainty=0.1)
    cur = rs.limits(a, LimitSettings(method=LimitMethod.CURRIE), w=0.01, w_rel_uncertainty=0.1)
    assert iso.method is LimitMethod.ISO_11929 and cur.method is LimitMethod.CURRIE
    assert iso.decision_threshold == pytest.approx(cur.decision_threshold, rel=1e-12)
    assert iso.detection_limit is not None and cur.detection_limit is not None
    assert iso.detection_limit > cur.detection_limit  # ISO includes u(w)


def test_proposals_group_and_fit() -> None:
    counts = np.rint(
        gaussian_counts(
            400, [(100.0, 4.0, 5000.0), (300.0, 4.0, 6000.0), (309.0, 4.0, 3000.0)], 50.0
        )
    ).astype(int)
    s = make_raw(counts)
    found = find_peak_candidates(s, PeakSearchSettings(fwhm_channels=(3.0, 4.0, 5.0, 6.0)))
    proposals = rs.propose_rois(s, found)
    kinds = sorted(p.roi.kind.value for p in proposals)
    assert kinds == ["multiplet", "single"]
    single = next(p.roi for p in proposals if p.roi.kind is rs.RoiKind.SINGLE)
    assert single.contains(100.0) and single.n_channels >= 2 * 3 * 3
    multi = next(p for p in proposals if p.roi.kind is rs.RoiKind.MULTIPLET)
    assert len(multi.roi.peak_channels) == 2 and "fitted together" in multi.reasons[0]
    assert len(rs.candidates_in(multi.roi, found)) == 2
    result = rs.fit_roi(s, single, FitSettings())
    assert result.converged
    with pytest.raises(ProcessingError, match="no peak"):
        rs.fit_roi(s, rs.make_roi(s, 150, 200), FitSettings())


def test_close_pairs_are_reported() -> None:
    """Two peaks 6 channels apart; the search estimates FWHM 3.7 (blended), so the pair is
    1.7 estimated FWHM apart: reported at a 2.0 threshold, not at 1.5. Widths come from
    the search until the resolution model supplies them (R12)."""
    counts = np.rint(gaussian_counts(200, [(100.0, 5.0, 8000.0), (106.0, 5.0, 8000.0)], 20.0))
    s = make_raw(counts.astype(int))
    found = find_peak_candidates(s, PeakSearchSettings(fwhm_channels=(3.0, 4.0, 5.0, 6.0)))
    (strict,) = rs.propose_rois(s, found, close_pair_fwhm=2.0)
    assert strict.roi.kind is rs.RoiKind.MULTIPLET
    assert any("possibly unresolved" in r for r in strict.reasons)
    (default,) = rs.propose_rois(s, found)
    assert not any("possibly unresolved" in r for r in default.reasons)


def test_rois_are_recorded_and_replayed(tmp_path: Path) -> None:
    from nsw.database.store import ProjectStore
    from nsw.services import project as ps

    s = make_raw(np.arange(100) % 7 + 20)
    store = ps.create_project(tmp_path / "p", "ROI test")
    store.add_spectrum(s, "s.csv")
    a = rs.make_roi(s, 10, 40, rs.RoiKind.SINGLE, (25.0,), (4.0,), "first")
    b = rs.make_roi(s, 50, 80)
    rs.record(store, a, "roi_create")
    rs.record(store, b, "roi_create")
    rs.record(store, a.moved_to(12, 45), "roi_edit")
    rs.record(store, b, "roi_delete")
    path = store.path
    store.close()
    store = ProjectStore.open(path)
    (only,) = rs.current_rois(store, s.data_sha256)
    assert (only.roi_id, only.first_channel, only.last_channel) == (a.roi_id, 12, 45)
    assert only.label == "first" and only.peak_channels == (25.0,)
    with pytest.raises(ProcessingError):
        rs.record(store, only, "roi_rename")
    store.close()


def test_resolution_model_widths_resolve_the_blended_pair_limitation() -> None:
    """With the true widths (FWHM 5) from a resolution model, the pair 6 channels apart is
    1.2 FWHM apart and is reported at the default 1.5 threshold."""
    from nsw.analysis.resolution import ResolutionVariable, WidthPoint, fit_resolution

    counts = np.rint(gaussian_counts(200, [(100.0, 5.0, 8000.0), (106.0, 5.0, 8000.0)], 20.0))
    s = make_raw(counts.astype(int))
    found = find_peak_candidates(s, PeakSearchSettings(fwhm_channels=(3.0, 4.0, 5.0, 6.0)))
    points = [WidthPoint(x, 5.0, 0.05) for x in (20.0, 60.0, 150.0, 190.0)]
    model = fit_resolution(points, ResolutionVariable.CHANNEL)
    (p,) = rs.propose_rois(s, found, expected_fwhm=rs.fwhm_from_resolution(model))
    assert p.roi.fwhm_channels == pytest.approx((5.0, 5.0), rel=1e-6)
    assert any("possibly unresolved" in r and "resolution model" in r for r in p.reasons)
    kev = fit_resolution(points, ResolutionVariable.ENERGY_KEV)
    with pytest.raises(ProcessingError, match="keV"):
        rs.fwhm_from_resolution(kev)


# ---- U3: peaks for a region, model comparison -------------------------------------------
def test_peaks_for_roi_sources() -> None:
    counts = np.rint(gaussian_counts(200, [(100.0, 5.0, 6000.0)], 40.0)).astype(int)
    s = make_raw(counts)
    roi = rs.make_roi(s, 80, 120)
    searched, source = rs.peaks_for_roi(s, roi)
    assert source == "candidates found by searching the region"
    assert searched.roi_id == roi.roi_id and len(searched.peak_channels) == 1
    assert abs(searched.peak_channels[0] - 100.0) < 0.5
    assert searched.kind is rs.RoiKind.CUSTOM  # an analyst's custom region stays custom
    found = find_peak_candidates(s, PeakSearchSettings(fwhm_channels=(4.0, 6.0)))
    from_search, source = rs.peaks_for_roi(s, roi, found)
    assert source == "candidates of the peak search"
    kept, source = rs.peaks_for_roi(s, from_search)
    assert kept is from_search and source == "peaks of the region"
    with pytest.raises(ProcessingError, match="no peak candidate"):
        rs.peaks_for_roi(s, rs.make_roi(s, 10, 40))


def test_add_peak_to_a_region() -> None:
    s = make_raw(np.rint(gaussian_counts(120, [(60.0, 4.0, 3000.0)], 30.0)).astype(int))
    roi = rs.make_roi(s, 40, 80, rs.RoiKind.SINGLE, (60.0,), (4.0,))
    two = rs.add_peak(roi, s, 52.0)
    assert two.peak_channels == (52.0, 60.0) and two.fwhm_channels == (4.0, 4.0)
    assert two.kind is rs.RoiKind.MULTIPLET and two.roi_id == roi.roi_id
    with pytest.raises(ProcessingError, match="outside"):
        rs.add_peak(roi, s, 90.0)
    with pytest.raises(ProcessingError, match="already"):
        rs.add_peak(roi, s, 60.2)


def test_model_comparison_keeps_the_simplest_adequate_model() -> None:
    """Flat continuum: constant is kept. Steep slope: linear is preferred."""
    rng = np.random.default_rng(11)
    flat = make_raw(rng.poisson(gaussian_counts(160, [(80.0, 5.0, 5000.0)], 100.0)))
    roi = rs.make_roi(flat, 55, 105, rs.RoiKind.SINGLE, (80.0,), (5.0,))
    cmp = rs.compare_models(flat, roi, FitSettings())
    assert cmp.preferred == "constant continuum", cmp.reason
    assert [f.name for f in cmp.fits] == [name for name, _, _ in rs.MODEL_VARIANTS]
    rows = rs.comparison_rows(cmp)
    assert rows[0][5] == "preferred" and len(rows) == 3
    x = np.arange(1, 161)
    sloped_mean = gaussian_counts(160, [(80.0, 5.0, 5000.0)], 0.0) + 40.0 + 4.0 * x
    sloped = make_raw(rng.poisson(sloped_mean))
    roi2 = rs.make_roi(sloped, 55, 105, rs.RoiKind.SINGLE, (80.0,), (5.0,))
    cmp2 = rs.compare_models(sloped, roi2, FitSettings())
    assert cmp2.preferred.startswith("linear continuum"), cmp2.reason
    with pytest.raises(ProcessingError):
        rs.compare_models(flat, rs.make_roi(flat, 10, 40), FitSettings())


def test_tail_test_service_on_a_symmetric_peak() -> None:
    rng = np.random.default_rng(2)
    s = make_raw(rng.poisson(gaussian_counts(200, [(100.0, 5.0, 40000.0)], 30.0)))
    roi = rs.make_roi(s, 80, 120, rs.RoiKind.SINGLE, (100.0,), (5.0,))
    test = rs.run_tail_test(s, roi, replicas=30)
    assert test.preferred == "gaussian"
    text = rs.tail_text(test)
    assert "Gaussian fit stays" in text and "bootstrap" in text
    with pytest.raises(ProcessingError):
        rs.run_tail_test(s, rs.make_roi(s, 10, 40))
