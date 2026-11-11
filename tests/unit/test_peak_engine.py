"""Deterministic tests of the peak-search filter and the fit model/estimators.

Reference values are analytic (stated in comments) or come from an independent
calculation written in the test itself (finite differences, brute-force sums).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pytest
from scipy.special import ndtr
from scipy.stats import poisson

from nsw.analysis import fitting
from nsw.analysis.fitting import (
    BackgroundShape,
    FitMethod,
    FitSettings,
    fit_candidates,
    fit_peaks,
    fit_peaks_joint,
    fit_region_for,
    model_and_jacobian,
    multiplet_evidence,
    poisson_deviance_moments,
)
from nsw.analysis.peaks import (
    FWHM_PER_SIGMA,
    PeakSearchSettings,
    estimate_fwhm_channels,
    filter_response,
    find_peak_candidates,
    search_kernel,
)
from nsw.core import DataOrigin, ProcessingError, Spectrum, SpectrumKind
from nsw.core.spectrum import ProcessingStep
from tests.conftest import make_raw, make_source


def binned_gaussian(x: np.ndarray, area: float, mu: float, sigma: float) -> np.ndarray:  # type: ignore[type-arg]
    """Independent reference: exact channel integrals of a Gaussian."""
    return area * (ndtr((x + 0.5 - mu) / sigma) - ndtr((x - 0.5 - mu) / sigma))


def derived(values: Any, variance: Any, **kw: Any) -> Spectrum:
    """A float-valued spectrum with explicit variance (as a net spectrum has)."""
    return Spectrum(
        counts=np.asarray(values, dtype=float),
        variance=np.asarray(variance, dtype=float),
        kind=SpectrumKind.NET,
        origin=DataOrigin.EXPERIMENTAL,
        live_time_s=100.0,
        real_time_s=100.0,
        source=make_source(),
        provenance=(ProcessingStep("test-construction"),),
        **kw,
    )


X = np.arange(1.0, 201.0)


# ---------------------------------------------------------------- search filter
@pytest.mark.parametrize("sigma", [0.9, 2.5, 7.0])
def test_kernel_has_zero_area_and_is_symmetric(sigma: float) -> None:
    k = search_kernel(sigma)
    assert abs(k.sum()) < 1e-12
    np.testing.assert_allclose(k, k[::-1], atol=1e-15)
    assert k.size == 2 * max(2, math.ceil(4 * sigma)) + 1


def test_filter_ignores_constant_and_linear_continuum() -> None:
    y = 500.0 + 3.0 * X
    r, v = filter_response(y, y, sigma=3.0)
    assert np.max(np.abs(r)) < 1e-8  # zero area + symmetry => no response to a + b x
    assert np.all(v[20:-20] > 0)


def test_filter_response_matches_analytic_value() -> None:
    # R = N s^3 / (s^2 + p^2)^(3/2), with p^2 the peak variance plus 1/12 from binning
    area, mu, p, s = 1.0e4, 100.0, 3.0, 4.0
    y = binned_gaussian(X, area, mu, p)
    r, _ = filter_response(y, y, sigma=s)
    expected = area * s**3 / (s**2 + p**2 + 1 / 12) ** 1.5
    assert r[99] == pytest.approx(expected, rel=2e-3)
    assert int(np.argmax(r)) == 99  # channel 100


def test_search_recovers_noise_free_peak_parameters() -> None:
    area, mu, p = 2.0e4, 100.3, 2.5
    y = np.round(binned_gaussian(X, area, mu, p) + 40.0).astype(np.int64)
    result = find_peak_candidates(make_raw(y), PeakSearchSettings(fwhm_channels=(6.0, 12.0)))
    (c,) = result.candidates
    assert c.channel == pytest.approx(mu, abs=0.05)
    assert c.fwhm_estimate == pytest.approx(p * FWHM_PER_SIGMA, rel=0.03)
    assert c.area_estimate == pytest.approx(area, rel=0.05)
    assert c.prominence == pytest.approx(area / (p * math.sqrt(2 * math.pi)), rel=0.05)
    assert c.significance > 50 and 0 < c.channel_uncertainty < 0.1
    assert c.energy_kev is None  # experimental spectrum without verified calibration
    assert any("channel numbers" in w for w in result.warnings)


def test_search_finds_nothing_in_empty_or_flat_spectra() -> None:
    settings = PeakSearchSettings(fwhm_channels=(5.0,))
    empty = find_peak_candidates(make_raw(np.zeros(300, dtype=int)), settings)
    assert empty.candidates == ()
    assert any("empty" in w for w in empty.warnings)  # significance caveat is reported
    assert find_peak_candidates(make_raw(np.full(300, 1000)), settings).candidates == ()


def test_search_groups_overlapping_candidates() -> None:
    y = binned_gaussian(X, 2e4, 80.0, 2.0) + binned_gaussian(X, 1e4, 90.0, 2.0)
    y += binned_gaussian(X, 2e4, 160.0, 2.0) + 20.0
    result = find_peak_candidates(
        make_raw(np.round(y).astype(np.int64)), PeakSearchSettings(fwhm_channels=(5.0,))
    )
    assert [round(c.channel) for c in result.candidates] == [80, 90, 160]
    assert [c.group for c in result.candidates] == [0, 0, 1]  # 10 ch apart < 3 FWHM
    assert [len(g) for g in result.groups()] == [2, 1]


def test_search_region_and_settings_validation() -> None:
    s = make_raw(np.full(300, 10))
    with pytest.raises(ProcessingError, match="at least one detection scale"):
        PeakSearchSettings(fwhm_channels=())
    with pytest.raises(ProcessingError, match="at least 2 channels"):
        PeakSearchSettings(fwhm_channels=(1.0,))
    with pytest.raises(ProcessingError, match="threshold"):
        PeakSearchSettings(fwhm_channels=(4.0,), threshold=0.0)
    with pytest.raises(ProcessingError, match="not inside"):
        find_peak_candidates(s, PeakSearchSettings(fwhm_channels=(4.0,), last_channel=999))
    wide = find_peak_candidates(make_raw(np.full(30, 10)), PeakSearchSettings((40.0,)))
    assert any("too wide" in w for w in wide.warnings)


def test_search_restricted_to_region() -> None:
    y = binned_gaussian(X, 2e4, 50.0, 2.0) + binned_gaussian(X, 2e4, 150.0, 2.0) + 20.0
    s = make_raw(np.round(y).astype(np.int64))
    r = find_peak_candidates(
        s, PeakSearchSettings(fwhm_channels=(5.0,), first_channel=100, last_channel=200)
    )
    assert [round(c.channel) for c in r.candidates] == [150]
    assert (r.first_channel, r.last_channel) == (100, 200)


def test_estimate_fwhm_channels() -> None:
    y = np.round(binned_gaussian(X, 5e4, 100.0, 3.0) + 30.0).astype(np.int64)
    assert estimate_fwhm_channels(make_raw(y)) == pytest.approx(3.0 * FWHM_PER_SIGMA, rel=0.1)
    assert estimate_fwhm_channels(make_raw(np.full(200, 50))) is None
    assert estimate_fwhm_channels(make_raw(np.full(8, 50))) is None


# ---------------------------------------------------------------- model
def layout(k: int, **kw: Any) -> Any:
    d: dict[str, Any] = dict(shared_width=True, step=False, background=BackgroundShape.LINEAR)
    d.update(kw)
    return fitting._Layout(k, **d)


def test_model_equals_independent_binned_gaussian_plus_line() -> None:
    lay = layout(1)
    theta = np.array([1000.0, 100.2, 2.0, 5.0, 9.0])  # area, centroid, sigma, bL, bR
    m, _, continuum = model_and_jacobian(X, theta, lay)
    line = 5.0 + 4.0 * (X - 1.0) / 199.0
    np.testing.assert_allclose(continuum, line, rtol=1e-12)
    np.testing.assert_allclose(m, binned_gaussian(X, 1000.0, 100.2, 2.0) + line, rtol=1e-12)
    assert (m - continuum).sum() == pytest.approx(1000.0, rel=1e-12)  # area is the integral


@pytest.mark.parametrize(
    ("lay", "theta"),
    [
        (layout(1), [1000.0, 100.2, 2.0, 5.0, 9.0]),
        (layout(2, shared_width=False), [900.0, 400.0, 95.0, 104.5, 2.0, 3.1, 5.0, 9.0]),
        (layout(2, step=True), [900.0, 400.0, 95.0, 104.5, 2.4, 0.03, 5.0, 9.0]),
        (layout(1, background=BackgroundShape.CONSTANT), [50.0, 60.0, 1.2, 3.0]),
        (layout(1, background=BackgroundShape.NONE, step=True), [50.0, 60.0, 1.2, 0.2]),
    ],
)
def test_analytic_jacobian_matches_finite_differences(lay: Any, theta: list[float]) -> None:
    t = np.array(theta)
    assert len(lay.names()) == lay.n_parameters == t.size
    _, jac, _ = model_and_jacobian(X, t, lay)
    for i in range(t.size):
        h = 1e-6 * max(1.0, abs(t[i]))
        up, dn = t.copy(), t.copy()
        up[i] += h
        dn[i] -= h
        numeric = (model_and_jacobian(X, up, lay)[0] - model_and_jacobian(X, dn, lay)[0]) / (2 * h)
        np.testing.assert_allclose(jac[:, i], numeric, rtol=1e-6, atol=1e-7)


def test_step_is_on_the_low_side_and_proportional_to_amplitude() -> None:
    lay = layout(1, step=True, background=BackgroundShape.NONE)
    area, sigma, f = 1000.0, 2.0, 0.05
    m, _, continuum = model_and_jacobian(X, np.array([area, 100.0, sigma, f]), lay)
    amplitude = area / (sigma * math.sqrt(2 * math.pi))
    assert continuum[10] == pytest.approx(f * amplitude, rel=1e-9)  # far below the peak
    assert continuum[99] == pytest.approx(0.5 * f * amplitude, rel=1e-9)  # at the centroid
    assert continuum[-1] < 1e-12  # nothing above the peak
    assert np.all(m >= continuum)


# ---------------------------------------------------------------- estimators
def asimov(theta: list[float], lay: Any) -> tuple[Spectrum, np.ndarray]:  # type: ignore[type-arg]
    m, jac, _ = model_and_jacobian(X, np.array(theta), lay)
    return derived(m, m), jac


def test_wls_on_exact_data_returns_exact_parameters_and_fisher_covariance() -> None:
    truth = [5000.0, 100.3, 2.5, 40.0, 60.0]
    lay = layout(1)
    s, jac = asimov(truth, lay)
    r = fit_peaks(s, 1, 200, [99.0], 5.0, FitSettings(method=FitMethod.WLS))
    assert r.ok and r.method == "wls" and r.ndf == 195
    np.testing.assert_allclose(r.values, truth, rtol=1e-6)
    assert r.statistic == pytest.approx(0.0, abs=1e-8) and r.pearson_chi2 < 1e-8
    # independent covariance: inverse of J^T diag(1/var) J
    m = s.counts
    cov = np.linalg.inv(jac.T @ (jac / m[:, None]))
    assert r.covariance is not None
    np.testing.assert_allclose(r.covariance, cov, rtol=1e-5)
    p = r.peaks[0]
    assert p.area_unc == pytest.approx(math.sqrt(cov[0, 0]), rel=1e-5)
    assert p.fwhm == pytest.approx(2.5 * FWHM_PER_SIGMA, rel=1e-6)
    assert p.fwhm_unc == pytest.approx(FWHM_PER_SIGMA * math.sqrt(cov[2, 2]), rel=1e-5)
    # amplitude A = N / (sigma sqrt(2 pi)) with first-order propagation including cov(N, sigma)
    a = 5000.0 / (2.5 * math.sqrt(2 * math.pi))
    g = np.array([a / 5000.0, -a / 2.5])
    assert p.amplitude == pytest.approx(a, rel=1e-6)
    assert p.amplitude_unc == pytest.approx(
        math.sqrt(g @ cov[np.ix_([0, 2], [0, 2])] @ g), rel=1e-5
    )
    assert r.correlation("area1", "sigma") == pytest.approx(
        cov[0, 2] / math.sqrt(cov[0, 0] * cov[2, 2]), rel=1e-5
    )
    assert p.energy_kev is None


def test_poisson_fit_of_integer_data_is_close_to_generating_parameters() -> None:
    truth = [20000.0, 100.3, 2.5, 40.0, 60.0]
    m, _, _ = model_and_jacobian(X, np.array(truth), layout(1))
    r = fit_peaks(make_raw(np.round(m).astype(np.int64)), 60, 140, [101.0], 6.0)
    assert r.ok and r.method == "poisson"
    assert r.peaks[0].area == pytest.approx(20000.0, rel=2e-3)
    assert r.peaks[0].centroid == pytest.approx(100.3, abs=0.01)
    assert r.reduced_chi2 < 0.2  # rounding noise only
    assert r.fitted.shape == r.data.shape == r.residuals.shape == (81,)
    assert (r.first_channel, r.last_channel) == (60, 140)


def test_poisson_handles_empty_channels_without_pseudo_counts() -> None:
    # 12 counts in three channels, everything else empty; pure peak model
    y = np.zeros(60, dtype=np.int64)
    y[29:32] = (3, 6, 3)
    r = fit_peaks(make_raw(y), 1, 60, [30.0], 3.0, FitSettings(background=BackgroundShape.NONE))
    assert r.converged
    assert r.peaks[0].area == pytest.approx(12.0, rel=1e-4)  # ML area = total counts
    assert r.peaks[0].centroid == pytest.approx(31.0, abs=1e-3)
    assert r.peaks[0].area_unc == pytest.approx(math.sqrt(12.0), rel=1e-3)
    assert "deviance" in r.gof_method  # chi-square tables not used at these counts
    assert np.array_equal(r.data, y.astype(float))  # data untouched


def test_poisson_refused_for_net_data_and_wls_refused_for_zero_variance() -> None:
    net = derived(np.full(50, -1.0), np.full(50, 2.0))
    with pytest.raises(ProcessingError, match="Poisson likelihood applies to measured counts"):
        fit_peaks(net, 1, 50, [25.0], 4.0, FitSettings(method=FitMethod.POISSON))
    with pytest.raises(ProcessingError, match="zero variance"):
        fit_peaks(
            make_raw(np.zeros(50, dtype=int)), 1, 50, [25.0], 4.0, FitSettings(method=FitMethod.WLS)
        )


def test_auto_method_and_warnings() -> None:
    m, _, _ = model_and_jacobian(X, np.array([5000.0, 100.0, 2.5, 40.0, 60.0]), layout(1))
    net = fit_peaks(derived(m, m), 60, 140, [100.0], 6.0)
    assert net.method == "wls"
    assert any("scaling factor" in w for w in net.warnings)
    counts = make_raw(np.round(m).astype(np.int64))
    wls = fit_peaks(counts, 60, 140, [100.0], 6.0, FitSettings(method=FitMethod.WLS))
    assert any("1/n weights" in w for w in wls.warnings)
    assert any("channels" in w for w in wls.warnings)


def test_invalid_fit_requests() -> None:
    s = make_raw(np.full(100, 10))
    with pytest.raises(ProcessingError, match="outside the spectrum"):
        fit_peaks(s, 0, 50, [25.0], 4.0)
    with pytest.raises(ProcessingError, match="outside the fit region"):
        fit_peaks(s, 10, 50, [60.0], 4.0)
    with pytest.raises(ProcessingError, match="at least one peak"):
        fit_peaks(s, 10, 50, [], 4.0)
    with pytest.raises(ProcessingError, match="widen it"):
        fit_peaks(s, 10, 14, [12.0], 4.0)
    with pytest.raises(ProcessingError, match="one FWHM guess per peak"):
        fit_peaks(s, 10, 60, [20.0, 30.0], [4.0])
    with pytest.raises(ProcessingError, match="must be > 0"):
        fit_peaks(s, 10, 60, [20.0], 0.0)
    with pytest.raises(ProcessingError, match="max_iterations"):
        FitSettings(max_iterations=0)
    with pytest.raises(ProcessingError, match="fit settings"):
        FitSettings(background="quadratic")  # type: ignore[arg-type]
    plain = FitSettings(method="wls", background="constant")  # type: ignore[arg-type]
    assert plain.method is FitMethod.WLS and plain.background is BackgroundShape.CONSTANT


def test_no_peak_is_reported_as_not_significant() -> None:
    r = fit_peaks(make_raw(np.full(100, 50)), 20, 80, [50.0], 4.0)
    # exactly flat data: no exception, an area compatible with zero, and an explicit notice
    p = r.peaks[0]
    assert not r.ok or abs(p.area) < 2 * p.area_unc
    assert not r.ok or p.significance < 3
    assert not r.ok or any("not significant" in w for w in r.warnings)


def test_fit_region_for_clips_to_spectrum() -> None:
    s = make_raw(np.full(100, 10))
    assert fit_region_for(s, [50.0], 4.0) == (38, 62)
    assert fit_region_for(s, [5.0, 97.0], 4.0) == (1, 100)
    with pytest.raises(ProcessingError):
        fit_region_for(s, [], 4.0)


def test_fit_candidates_rejects_search_of_another_spectrum() -> None:
    y = np.round(binned_gaussian(X, 2e4, 100.0, 2.5) + 40.0).astype(np.int64)
    a, b = make_raw(y), make_raw(y + 1)
    search = find_peak_candidates(a, PeakSearchSettings(fwhm_channels=(6.0,)))
    (fit,) = fit_candidates(a, search)
    assert fit.result is not None and fit.error is None and fit.result.ok
    assert fit.result.peaks[0].area == pytest.approx(2e4, rel=0.02)
    with pytest.raises(ProcessingError, match="different spectrum"):
        fit_candidates(b, search)


# ---------------------------------------------------------------- statistics helpers
def test_deviance_moments_against_brute_force_sum() -> None:
    for m in (0.01, 0.6, 3.0, 25.0):
        n = np.arange(0, 400)
        pmf = poisson.pmf(n, m)
        with np.errstate(divide="ignore", invalid="ignore"):
            d = 2 * (m - n + np.where(n > 0, n * np.log(n / m), 0.0))
        mean, var = poisson_deviance_moments(np.array([m]))
        assert mean[0] == pytest.approx(float((pmf * d).sum()), rel=1e-9)
        assert var[0] == pytest.approx(float((pmf * d * d).sum() - (pmf * d).sum() ** 2), rel=1e-9)
    mean, var = poisson_deviance_moments(np.array([1e4]))
    assert mean[0] == pytest.approx(1.0, abs=1e-3) and var[0] == pytest.approx(2.0)
    low, _ = poisson_deviance_moments(np.array([0.01]))
    assert low[0] < 0.2  # far from the chi-square expectation of 1


def test_runs_statistic() -> None:
    assert fitting._runs_z(np.array([1.0, -1, 1, -1, 1, -1, 1, -1])) == pytest.approx(
        (8 - 5) / math.sqrt(2 * 16 * (32 - 8) / (64 * 7))
    )
    assert fitting._runs_z(np.ones(5)) is None
    z = fitting._runs_z(np.array([1.0] * 10 + [-1.0] * 10))
    assert z is not None and z < -3  # two long runs: strong structure


def test_joint_profile_solution_is_a_stationary_point() -> None:
    rng = np.random.default_rng(1)
    g = rng.poisson(6.0, 200).astype(float)
    b = rng.poisson(3.0, 200).astype(float)
    g[:20] = 0.0
    b[10:40] = 0.0
    s = rng.uniform(-1.0, 8.0, 200)
    alpha = 0.7
    stat = fitting._JointPoisson(g, b, alpha)
    beta = stat.beta(s)
    mu = alpha * beta + s
    assert np.all(beta >= 0) and np.all(mu >= -1e-12)
    free = (mu > 1e-9) & (beta > 1e-9)
    # d logL / d beta = alpha G / mu - alpha + B / beta - 1 = 0 where no constraint is active
    grad = alpha * g[free] / mu[free] - alpha + b[free] / beta[free] - 1.0
    assert np.max(np.abs(grad)) < 1e-9
    # numerical derivative of the profile log-likelihood equals the returned score
    h = 1e-6

    def loglike(sv: np.ndarray) -> np.ndarray:  # type: ignore[type-arg]
        d, _, _ = stat.terms(sv)
        return -0.5 * d

    _, u, w = stat.terms(s)
    inner = np.abs(mu) > 1e-3
    numeric = (loglike(s + h) - loglike(s - h)) / (2 * h)
    np.testing.assert_allclose(u[inner], numeric[inner], rtol=1e-4, atol=1e-5)
    assert np.all(w > 0)


T_A, T_B = datetime(2026, 4, 23, 13, 0, 0), datetime(2026, 4, 24, 12, 0, 0)


def test_joint_fit_recovers_source_peak_and_enforces_background_rules() -> None:
    x = np.arange(1.0, 121.0)
    ambient = 30.0 + 0.0 * x
    peak = binned_gaussian(x, 4000.0, 60.4, 2.5)
    gross = make_raw(
        np.round(ambient + peak).astype(np.int64),
        live_time_s=1000.0,
        real_time_s=1001.0,
        start_time=T_B,
        source=make_source("gross.csv"),
    )
    bg_raw = make_raw(
        np.round(2 * ambient).astype(np.int64),
        live_time_s=2000.0,
        real_time_s=2001.0,
        start_time=T_A,
        source=make_source("bg.csv"),
    )
    with pytest.raises(ProcessingError, match="not been designated"):
        fit_peaks_joint(gross, bg_raw, 30, 90, [60.0], 6.0)
    with pytest.raises(ProcessingError, match="same acquisition"):
        fit_peaks_joint(gross, gross.designated_as_background(), 30, 90, [60.0], 6.0)
    r = fit_peaks_joint(gross, bg_raw.designated_as_background(), 30, 90, [60.0], 6.0)
    assert r.ok and r.method == "joint-poisson"
    assert r.peaks[0].area == pytest.approx(4000.0, rel=2e-3)
    assert r.peaks[0].centroid == pytest.approx(60.4, abs=0.01)
    # source continuum is zero here: both end values compatible with 0
    for _, value, unc in r.background:
        assert abs(value) < 3 * unc
    np.testing.assert_allclose(r.fitted, (ambient + peak)[29:90], rtol=0.02)
    # uncertainty exceeds the pure-Poisson value because the background is also uncertain
    assert r.peaks[0].area_unc > math.sqrt(4000.0)


# ---------------------------------------------------------------- multiplets
def test_multiplet_evidence_rejects_superfluous_component() -> None:
    y = np.round(binned_gaussian(X, 2e4, 100.0, 2.5) + 40.0).astype(np.int64)
    ev = multiplet_evidence(make_raw(y), 70, 130, [98.0, 102.0], 6.0)
    assert not ev.supported
    assert "must not be reported" in ev.statement or "do not require" in ev.statement
    with pytest.raises(ProcessingError, match="at least two"):
        multiplet_evidence(make_raw(y), 70, 130, [100.0], 6.0)


def test_multiplet_evidence_supports_clear_doublet() -> None:
    y = binned_gaussian(X, 2e4, 95.0, 2.5) + binned_gaussian(X, 1e4, 107.0, 2.5) + 40.0
    ev = multiplet_evidence(make_raw(np.round(y).astype(np.int64)), 70, 130, [95.0, 107.0], 6.0)
    assert ev.supported and ev.n_components == 2 and ev.removed_parameters == 2
    assert ev.delta_bic > 1000 and ev.delta_aic > ev.delta_bic
    assert ev.separations_fwhm[0] == pytest.approx(12.0 / (2.5 * FWHM_PER_SIGMA), rel=0.01)
    assert abs(ev.area_correlations[0]) < 0.2
    assert "not proof" in ev.statement


def test_display_only_spectrum_is_refused() -> None:
    from nsw.analysis.background import smooth_for_display

    smooth = smooth_for_display(make_raw(np.full(100, 50)))
    with pytest.raises(ProcessingError, match="display-only"):
        find_peak_candidates(smooth, PeakSearchSettings(fwhm_channels=(4.0,)))
    with pytest.raises(ProcessingError, match="display-only"):
        fit_peaks(smooth, 10, 60, [30.0], 4.0)
