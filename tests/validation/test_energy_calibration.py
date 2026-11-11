"""Validation of the energy-calibration regression.

References: closed-form weighted straight-line formulas written out in the test,
``numpy.polyfit`` as an independent implementation, and Monte Carlo ensembles with
known truth. Reference "energies" are synthetic test values, not nuclear data.

Tolerances: algebraic identities 1e-9 relative; Monte Carlo quantities within five
standard errors of their ideal values (pull mean 0 +- 5/sqrt(n), pull sd 1 +- 5/sqrt(2n),
coverage 0.6827 +- 5 sqrt(p(1-p)/n)).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from nsw.analysis.energy_calibration import (
    compare_orders,
    fit_energy_calibration,
    position_stability,
)
from nsw.analysis.fitting import fit_peaks, fit_region_for
from nsw.core import (
    CalibrationOrigin,
    CalibrationPoint,
    CalibrationStatus,
    ProcessingError,
)
from nsw.simulation import presets
from nsw.simulation.generator import generate

SRC = "synthetic test value"


def pt(
    ch: float, e: float, uch: float = 0.01, ue: float = 0.001, label: str = ""
) -> CalibrationPoint:
    return CalibrationPoint(
        channel=ch,
        channel_uncertainty=uch,
        reference_value=e,
        reference_uncertainty=ue,
        reference_source=SRC,
        label=label,
    )


# ---------------------------------------------------------------- algebra
def test_straight_line_matches_closed_form_weighted_regression() -> None:
    x = np.array([100.0, 400.0, 900.0, 2500.0])
    y = np.array([25.6, 100.4, 225.7, 625.3])
    ue = np.array([0.05, 0.02, 0.04, 0.10])
    fit = fit_energy_calibration([pt(a, b, 0.0, c) for a, b, c in zip(x, y, ue, strict=True)])
    # textbook weighted least squares for y = a + b x
    w = 1 / ue**2
    s, sx, sy = w.sum(), (w * x).sum(), (w * y).sum()
    sxx, sxy = (w * x * x).sum(), (w * x * y).sum()
    delta = s * sxx - sx**2
    a, b = (sxx * sy - sx * sxy) / delta, (s * sxy - sx * sy) / delta
    np.testing.assert_allclose(fit.coefficients, [a, b], rtol=1e-10)
    np.testing.assert_allclose(
        fit.covariance, [[sxx / delta, -sx / delta], [-sx / delta, s / delta]], rtol=1e-9
    )
    chi2 = float((w * (y - a - b * x) ** 2).sum())
    assert fit.chi2 == pytest.approx(chi2, rel=1e-9) and fit.ndf == 2
    assert fit.iterations <= 2  # no channel uncertainty: weights do not depend on the slope
    np.testing.assert_allclose(fit.residuals_kev, y - a - b * x, atol=1e-10)


@pytest.mark.parametrize("order", [1, 2, 3])
def test_agrees_with_numpy_polyfit(order: int) -> None:
    rng = np.random.default_rng(5)
    x = np.sort(rng.uniform(50, 8000, 9))
    y = 0.4 + 0.25 * x + 2e-7 * x**2 + rng.normal(0, 0.01, 9)
    ue = rng.uniform(0.005, 0.02, 9)
    fit = fit_energy_calibration(
        [pt(a, b, 0.0, c) for a, b, c in zip(x, y, ue, strict=True)], order
    )
    ref, cov = np.polyfit(x, y, order, w=1 / ue, cov="unscaled")
    np.testing.assert_allclose(fit.coefficients, ref[::-1], rtol=2e-6)
    np.testing.assert_allclose(np.diag(fit.covariance), np.diag(cov)[::-1], rtol=1e-5)


def test_effective_variance_includes_channel_uncertainty() -> None:
    # exact line E = 1 + 0.5 ch; channel uncertainty 0.2 -> 0.1 keV, energy uncertainty 0.1 keV
    pts = [pt(c, 1 + 0.5 * c, 0.2, 0.1) for c in (100.0, 200.0, 300.0, 400.0)]
    fit = fit_energy_calibration(pts)
    np.testing.assert_allclose(fit.coefficients, [1.0, 0.5], atol=1e-10)
    np.testing.assert_allclose(fit.effective_uncertainties_kev, math.sqrt(0.02), rtol=1e-9)
    assert fit.chi2 == pytest.approx(0.0, abs=1e-12)
    # predicted energy: value, uncertainty from covariance, plus gain * channel uncertainty
    e, u, extrapolated = fit.energy([250.0, 500.0])
    np.testing.assert_allclose(e, [126.0, 251.0])
    assert list(extrapolated) == [False, True]
    # at the weighted centre (ch 250) u = sigma / sqrt(n)
    assert u[0] == pytest.approx(math.sqrt(0.02 / 4), rel=1e-9)
    assert u[1] > u[0]
    _, u_with_channel, _ = fit.energy(250.0, channel_uncertainty=0.4)
    assert u_with_channel[0] == pytest.approx(math.hypot(u[0], 0.5 * 0.4), rel=1e-9)
    assert fit.gain(250.0)[0] == pytest.approx(0.5)
    assert fit.channel_range == (100.0, 400.0)
    assert fit.energy_range_kev == pytest.approx((51.0, 201.0))


def test_record_uncertainty_matches_scaled_basis_uncertainty() -> None:
    rng = np.random.default_rng(11)
    x = np.linspace(200, 8000, 8)
    y = 0.5 + 0.25 * x + 3e-7 * x**2 + rng.normal(0, 0.01, 8)
    fit = fit_energy_calibration([pt(a, b, 0.02, 0.005) for a, b in zip(x, y, strict=True)], 2)
    record = fit.to_record(verified=True, detector_id="synthetic-hpge", synthetic=True)
    ch = np.array([150.0, 1000.0, 4000.0, 7900.0, 8500.0])
    e, u, _ = fit.energy(ch)
    np.testing.assert_allclose(record.energy_at(ch), e, rtol=1e-12)
    np.testing.assert_allclose(record.energy_uncertainty_at(ch), u, rtol=1e-5)
    np.testing.assert_allclose(record.gain_at(ch), fit.gain(ch), rtol=1e-12)
    assert record.status is CalibrationStatus.VERIFIED
    assert record.origin is CalibrationOrigin.FITTED and record.channel_range == (200.0, 8000.0)
    assert "SYNTHETIC" in record.note and "order 2" in record.method


# ---------------------------------------------------------------- rules
def test_one_peak_can_never_calibrate() -> None:
    with pytest.raises(ProcessingError, match="cannot determine both gain and offset"):
        fit_energy_calibration([pt(1868.0, 661.7)])
    with pytest.raises(ProcessingError, match="needs at least 3"):
        fit_energy_calibration([pt(100.0, 25.0), pt(200.0, 50.0)], order=2)
    with pytest.raises(ProcessingError, match="order must be"):
        fit_energy_calibration([pt(100.0, 25.0), pt(200.0, 50.0)], order=0)


def test_two_points_fit_but_cannot_be_verified() -> None:
    fit = fit_energy_calibration([pt(100.0, 25.5), pt(900.0, 225.5)])
    np.testing.assert_allclose(fit.coefficients, [0.5, 0.25], rtol=1e-12)
    assert fit.ndf == 0 and math.isnan(fit.p_value) and not fit.verifiable
    assert "no redundant point" in fit.why_not_verifiable()
    assert any("no redundancy" in w for w in fit.warnings)
    with pytest.raises(ProcessingError, match="cannot be verified"):
        fit.to_record(verified=True)
    record = fit.to_record(verified=False)
    assert record.status is CalibrationStatus.UNVERIFIED
    with pytest.raises(ProcessingError, match="without redundant points"):
        fit_energy_calibration([pt(100.0, 25.5), pt(900.0, 225.5)], inflate=True)


def test_invalid_points_are_rejected() -> None:
    with pytest.raises(ProcessingError, match="same channel"):
        fit_energy_calibration([pt(100.0, 25.0), pt(100.0, 50.0), pt(300.0, 75.0)])
    with pytest.raises(ProcessingError, match="must increase"):
        fit_energy_calibration([pt(100.0, 25.0), pt(200.0, 25.0), pt(300.0, 75.0)])
    with pytest.raises(ProcessingError, match="needs an uncertainty"):
        fit_energy_calibration([pt(100.0, 25.0, 0.0, 0.0), pt(200.0, 50.0), pt(300.0, 75.0)])


def test_wrong_assignment_is_reported_and_inflation_is_explicit() -> None:
    good = [pt(c, 0.5 + 0.25 * c) for c in (400.0, 1200.0, 2400.0, 4000.0, 6000.0)]
    bad = [*good[:2], pt(2400.0, 0.5 + 0.25 * 2400 + 0.3), *good[3:]]  # one line 0.3 keV off
    fit = fit_energy_calibration(bad)
    assert fit.poor_fit and not fit.verifiable and not fit.inflated
    assert "not consistent" in fit.why_not_verifiable()
    assert int(np.argmax(np.abs(fit.normalised_residuals))) == 2  # the culprit stands out
    with pytest.raises(ProcessingError, match="cannot be verified"):
        fit.to_record(verified=True)
    inflated = fit_energy_calibration(bad, inflate=True)
    assert inflated.inflated and inflated.verifiable
    np.testing.assert_allclose(inflated.coefficients, fit.coefficients)
    np.testing.assert_allclose(inflated.covariance, fit.covariance * fit.reduced_chi2, rtol=1e-12)
    assert inflated.birge_ratio == pytest.approx(math.sqrt(fit.chi2 / 3))
    assert "multiplied by chi2/ndf" in inflated.to_record(verified=True).method
    # consistent points are never inflated downwards
    calm = fit_energy_calibration(good, inflate=True)
    assert not calm.inflated


def test_order_comparison() -> None:
    rng = np.random.default_rng(3)
    x = np.array([300.0, 900.0, 1800.0, 3000.0, 4500.0, 6000.0, 7800.0])
    linear = [pt(c, 0.5 + 0.25 * c + rng.normal(0, 0.003), 0.01, 0.002) for c in x]
    cmp = compare_orders(linear)
    assert not cmp.justified and "not significant" in cmp.statement and cmp.higher is not None
    curved = [pt(c, 0.5 + 0.25 * c + 4e-7 * c * c + rng.normal(0, 0.003), 0.01, 0.002) for c in x]
    cmp = compare_orders(curved)
    assert cmp.justified and cmp.lower.poor_fit and cmp.higher is not None
    assert cmp.higher.coefficients[2] == pytest.approx(4e-7, rel=0.05)
    few = compare_orders(linear[:3])
    assert not few.justified and few.higher is None and "needs at least 4" in few.statement


# ---------------------------------------------------------------- synthetic spectra
def test_calibration_from_fitted_synthetic_peaks_recovers_generator_scale() -> None:
    specs = {s.synthetic_id: s for s in presets.hpge_scenarios()}
    res = generate(specs["hpge-isolated-peaks"])
    points = []
    for line in res.truth["lines"]:
        c, w = line["centroid_channel"], line["fwhm_channels"]
        first, last = fit_region_for(res.spectrum, [c], w)
        r = fit_peaks(res.spectrum, first, last, [c], w)
        assert r.ok
        points.append(pt(r.peaks[0].centroid, line["energy_kev"], r.peaks[0].centroid_unc, 0.0))
    fit = fit_energy_calibration(points)
    truth = np.array(res.truth["energy_calibration_kev"])  # [0.5, 0.25]
    pulls = (fit.coefficients - truth) / fit.coefficient_uncertainties
    assert np.all(np.abs(pulls) < 4), pulls
    assert fit.verifiable and not fit.poor_fit and fit.ndf == 3
    assert fit.coefficient_uncertainties[1] / fit.coefficients[1] < 1e-5  # gain to 10 ppm
    assert not compare_orders(points).justified  # the generator scale is exactly linear
    e, u, _ = fit.energy(3000.0)
    assert abs(e[0] - (0.5 + 0.25 * 3000.0)) < 4 * u[0] and u[0] < 0.01


def test_monte_carlo_coverage_of_coefficients_and_predictions() -> None:
    rng = np.random.default_rng(2026)
    x_true = np.array([350.0, 800.0, 1900.0, 3300.0, 3760.0, 5600.0])
    c_true = np.array([0.4, 0.3541, 1.5e-7])
    uch, ue = 0.02, 0.003
    n = 600
    pulls = np.empty((n, 3))
    pred = np.empty(n)
    p_values = np.empty(n)
    probe = 2500.0
    e_probe = np.polyval(c_true[::-1], probe)
    for i in range(n):
        ch = x_true + rng.normal(0, uch, x_true.size)  # measured positions
        e_ref = np.polyval(c_true[::-1], x_true) + rng.normal(0, ue, x_true.size)
        fit = fit_energy_calibration([pt(a, b, uch, ue) for a, b in zip(ch, e_ref, strict=True)], 2)
        pulls[i] = (fit.coefficients - c_true) / fit.coefficient_uncertainties
        e, u, _ = fit.energy(probe)
        pred[i] = (e[0] - e_probe) / u[0]
        p_values[i] = fit.p_value
    for column in (*pulls.T, pred):
        assert abs(column.mean()) < 5 / math.sqrt(n)
        assert abs(column.std(ddof=1) - 1) < 5 / math.sqrt(2 * n)
        cover = float(np.mean(np.abs(column) < 1))
        assert abs(cover - 0.6827) < 5 * math.sqrt(0.6827 * 0.3173 / n)
    assert abs(float(np.mean(p_values < 0.05)) - 0.05) < 5 * math.sqrt(0.05 * 0.95 / n)


# ---------------------------------------------------------------- stability


def test_position_stability() -> None:
    # hand calculation: equal uncertainties 0.1 -> mean 100.0, chi2 = (0.01+0.01+0)/0.01 = 2
    r = position_stability([99.9, 100.1, 100.0], [0.1, 0.1, 0.1])
    assert r.weighted_mean == pytest.approx(100.0) and r.chi2 == pytest.approx(2.0)
    assert r.ndf == 2 and r.p_value == pytest.approx(math.exp(-1.0))  # chi2 sf for 2 dof
    assert r.mean_uncertainty == pytest.approx(0.1 / math.sqrt(3))
    assert r.max_deviation == pytest.approx(0.1) and r.stable and "agree" in r.statement
    drift = position_stability([100.0, 100.0, 101.0], [0.05, 0.05, 0.05])
    assert not drift.stable and "not constant" in drift.statement
    assert drift.relative_spread == pytest.approx(drift.max_deviation / drift.weighted_mean)
    with pytest.raises(ProcessingError):
        position_stability([100.0], [0.1])
    with pytest.raises(ProcessingError):
        position_stability([100.0, 101.0], [0.1, 0.0])
