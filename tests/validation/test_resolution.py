"""Validation of the resolution model fit and of fixed-shape peak fits.

References: the generator's assumed resolution models (synthetic truth), an exact
noise-free construction, a hand-calculated two-point case, and Monte Carlo ensembles
(tolerances: five standard errors of the ideal values).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from nsw.analysis.fitting import FitSettings, fit_peaks, fit_region_for
from nsw.analysis.resolution import (
    ResolutionVariable,
    WidthPoint,
    fit_resolution,
    fwhm_in_kev,
)
from nsw.core import CalibrationStatus, ProcessingError
from nsw.services.validation import FitStudyCase, run_fit_study
from nsw.simulation import presets
from nsw.simulation.generator import SyntheticResult, generate

KEV, CH = ResolutionVariable.ENERGY_KEV, ResolutionVariable.CHANNEL


def model(x: np.ndarray, a: float, b: float, c: float = 0.0) -> np.ndarray:  # type: ignore[type-arg]
    return np.sqrt(a + b * x + c * x * x)


def points(x: np.ndarray, w: np.ndarray, u: float | np.ndarray) -> list[WidthPoint]:  # type: ignore[type-arg]
    uu = np.broadcast_to(u, x.shape)
    return [WidthPoint(float(a), float(b), float(c)) for a, b, c in zip(x, w, uu, strict=True)]


# ---------------------------------------------------------------- algebra
def test_exact_data_return_exact_coefficients() -> None:
    x = np.array([100.0, 300.0, 600.0, 1000.0, 1500.0])
    fit = fit_resolution(points(x, model(x, 0.75, 0.0025), 0.01), KEV)
    np.testing.assert_allclose(fit.coefficients, [0.75, 0.0025, 0.0], rtol=1e-9, atol=1e-12)
    assert fit.fitted_terms == (True, True, False) and fit.ndf == 3
    assert fit.chi2 == pytest.approx(0.0, abs=1e-12) and fit.unit == "keV"
    quad = fit_resolution(points(x, model(x, 0.5, 0.002, 1e-6), 0.01), KEV, quadratic=True)
    np.testing.assert_allclose(quad.coefficients, [0.5, 0.002, 1e-6], rtol=1e-7)
    assert quad.fitted_terms == (True, True, True) and quad.ndf == 2


def test_two_points_hand_calculation() -> None:
    # W^2 = a + b x through (100, 1.0) and (1300, 2.0): b = 3/1200, a = 1 - 0.25 = 0.75
    fit = fit_resolution([WidthPoint(100.0, 1.0, 0.02), WidthPoint(1300.0, 2.0, 0.03)], KEV)
    np.testing.assert_allclose(fit.coefficients[:2], [0.75, 0.0025], rtol=1e-10)
    assert fit.ndf == 0 and math.isnan(fit.p_value) and not fit.verifiable
    assert any("no redundancy" in w for w in fit.warnings)
    # the curve passes through the points, so the prediction there has their uncertainty
    w, u, extrapolated = fit.fwhm([100.0, 1300.0, 2000.0])
    np.testing.assert_allclose(w[:2], [1.0, 2.0], rtol=1e-10)
    np.testing.assert_allclose(u[:2], [0.02, 0.03], rtol=1e-8)
    assert list(extrapolated) == [False, False, True]
    rel, rel_u = fit.relative(1300.0)
    assert rel[0] == pytest.approx(2.0 / 1300.0) and rel_u[0] == pytest.approx(0.03 / 1300.0)
    with pytest.raises(ProcessingError, match="cannot be verified"):
        fit.to_record(verified=True)


def test_unsupported_terms_are_fixed_at_zero_and_reported() -> None:
    x = np.array([100.0, 300.0, 600.0, 1000.0, 1500.0])
    # NaI-like truth: a = 0 exactly; a tiny downward push makes the free estimate negative
    w = model(x, 0.0, 3.24)
    w[0] *= 0.99
    fit = fit_resolution(points(x, w, 0.2), KEV)
    assert fit.coefficients[0] == 0.0 and fit.fitted_terms == (False, True, False)
    assert fit.coefficient_uncertainties[0] == 0.0 and fit.ndf == 4
    assert fit.coefficients[1] == pytest.approx(3.24, rel=0.01)
    assert any("term(s) a not supported" in m for m in fit.warnings)
    # linear data offered a quadratic term: c ends at (or indistinguishable from) zero
    lin = model(x, 0.75, 0.0025)
    lin[2] *= 1.002
    quad = fit_resolution(points(x, lin, 0.01), KEV, quadratic=True)
    assert quad.coefficients[2] < 3 * max(quad.coefficient_uncertainties[2], 1e-12)


def test_invalid_input_is_rejected() -> None:
    good = [WidthPoint(100.0, 1.0, 0.02), WidthPoint(1300.0, 2.0, 0.03)]
    with pytest.raises(ProcessingError, match="at least two"):
        fit_resolution(good[:1], KEV)
    with pytest.raises(ProcessingError, match="3 model terms need at least 3"):
        fit_resolution(good, KEV, quadratic=True)
    with pytest.raises(ProcessingError, match="positive uncertainty"):
        fit_resolution([good[0], WidthPoint(1300.0, 2.0, 0.0)], KEV)
    with pytest.raises(ProcessingError, match="same position"):
        fit_resolution([good[0], WidthPoint(100.0, 1.1, 0.02)], KEV)
    with pytest.raises(ProcessingError, match="must be positive"):
        fit_resolution([good[0], WidthPoint(1300.0, -2.0, 0.03)], KEV)
    # widths that fall with x admit no model with b >= 0 and a >= 0 through them
    falling = fit_resolution([WidthPoint(100.0, 2.0, 0.01), WidthPoint(1300.0, 1.0, 0.01)], KEV)
    assert falling.fitted_terms == (True, False, False)  # constant width is the best admissible
    assert falling.poor_fit and falling.ndf == 1 and falling.chi2 > 1000  # and it says so
    channel_fit = fit_resolution(good, CH)
    with pytest.raises(ProcessingError, match="needs energies"):
        channel_fit.relative(500.0)
    assert channel_fit.unit == "channels"


def test_fwhm_in_kev_propagation() -> None:
    value, unc = fwhm_in_kev(4.0, 0.03, 0.25, 0.001)
    assert value == 1.0
    assert unc == pytest.approx(math.hypot(0.25 * 0.03, 4.0 * 0.001))
    with pytest.raises(ProcessingError):
        fwhm_in_kev(4.0, 0.03, 0.0)


# ---------------------------------------------------------------- synthetic spectra
def widths_from_scenario(name: str) -> tuple[SyntheticResult, list[WidthPoint], list[WidthPoint]]:
    specs = {s.synthetic_id: s for s in (*presets.hpge_scenarios(), *presets.nai_scenarios())}
    res = generate(specs[name])
    gain = res.truth["energy_calibration_kev"][1]
    kev, channel = [], []
    for line in res.truth["lines"]:
        c, w = line["centroid_channel"], line["fwhm_channels"]
        first, last = fit_region_for(res.spectrum, [c], w)
        r = fit_peaks(res.spectrum, first, last, [c], w)
        assert r.ok
        p = r.peaks[0]
        value, unc = fwhm_in_kev(p.fwhm, p.fwhm_unc, gain)  # synthetic truth gain: exact
        kev.append(WidthPoint(line["energy_kev"], value, unc, line["label"]))
        channel.append(WidthPoint(p.centroid, p.fwhm, p.fwhm_unc, line["label"]))
    return res, kev, channel


@pytest.mark.parametrize(
    "name",
    ["hpge-isolated-peaks-no-continuum", "hpge-high-count", "nai-isolated-peaks-no-continuum"],
)
def test_generator_resolution_model_is_recovered(name: str) -> None:
    res, kev, channel = widths_from_scenario(name)
    truth = res.truth["resolution_fwhm2_kev2"]
    fit = fit_resolution(kev, KEV)
    assert not fit.poor_fit and fit.ndf >= 3
    for line in res.truth["lines"]:
        w, u, extrapolated = fit.fwhm(line["energy_kev"])
        assert abs(w[0] - line["fwhm_kev"]) < 4 * u[0] and not extrapolated[0]
    for i, key in enumerate(("a", "b")):
        if fit.fitted_terms[i]:
            assert abs(fit.coefficients[i] - truth[key]) < 4 * fit.coefficient_uncertainties[i]
        else:
            assert truth[key] == 0.0  # only a truly absent term may be dropped
    # the same analysis in channel space describes the same widths (linear energy scale)
    gain = res.truth["energy_calibration_kev"][1]
    ch_fit = fit_resolution(channel, CH)
    for line in res.truth["lines"]:
        w, u, _ = ch_fit.fwhm(line["centroid_channel"])
        assert abs(w[0] * gain - line["fwhm_kev"]) < 4 * u[0] * gain
    record = fit.to_record(verified=True, detector_id="synthetic")
    assert record.status is CalibrationStatus.VERIFIED and record.model == "sqrt_quadratic"
    assert record.fwhm_unit == "keV" and len(record.points) == 5
    with pytest.raises(ProcessingError, match="channel units cannot be verified"):
        ch_fit.to_record(verified=True)
    assert ch_fit.to_record(verified=False).fwhm_unit == "channel"


def test_monte_carlo_coverage_of_predicted_width() -> None:
    rng = np.random.default_rng(77)
    x = np.array([120.0, 350.0, 660.0, 1170.0, 1330.0, 1800.0])
    a, b = 0.75, 0.0025
    truth = model(x, a, b)
    probe = 900.0
    n = 600
    pulls = np.empty((n, 3))
    p_values = np.empty(n)
    for i in range(n):
        u = 0.02 * truth
        fit = fit_resolution(points(x, truth + rng.normal(0, u), u), KEV)
        assert fit.fitted_terms == (True, True, False)
        w, uw, _ = fit.fwhm(probe)
        pulls[i] = [
            (fit.coefficients[0] - a) / fit.coefficient_uncertainties[0],
            (fit.coefficients[1] - b) / fit.coefficient_uncertainties[1],
            (w[0] - math.sqrt(a + b * probe)) / uw[0],
        ]
        p_values[i] = fit.p_value
    for column in pulls.T:
        assert abs(column.mean()) < 5 / math.sqrt(n)
        assert abs(column.std(ddof=1) - 1) < 5 / math.sqrt(2 * n)
        cover = float(np.mean(np.abs(column) < 1))
        assert abs(cover - 0.6827) < 5 * math.sqrt(0.6827 * 0.3173 / n)
    assert abs(float(np.mean(p_values < 0.05)) - 0.05) < 5 * math.sqrt(0.05 * 0.95 / n)


# ---------------------------------------------------------------- fixed-shape fits
def test_fixed_position_and_width_are_held_and_reported() -> None:
    res, _, _ = widths_from_scenario("hpge-isolated-peaks")
    line = res.truth["lines"][2]
    c, w = line["centroid_channel"], line["fwhm_channels"]
    first, last = fit_region_for(res.spectrum, [c], w)
    r = fit_peaks(
        res.spectrum, first, last, [c], w, FitSettings(fixed_centroids=True, fixed_width=True)
    )
    p = r.peaks[0]
    assert r.ok and p.centroid == c and p.fwhm == pytest.approx(w, rel=1e-12)
    assert p.centroid_unc == 0.0 and p.fwhm_unc == 0.0 and p.at_bound == ()
    assert abs(p.area - line["expected_peak_counts"]) < 4 * p.area_unc
    assert p.amplitude_unc == pytest.approx(p.area_unc / (p.sigma * math.sqrt(2 * math.pi)))
    free = fit_peaks(res.spectrum, first, last, [c], w)
    assert r.ndf == free.ndf + 2  # two parameters fewer are fitted
    assert p.area_unc <= free.peaks[0].area_unc  # conditional on the fixed shape
    assert any("held fixed" in m and "not included" in m for m in r.warnings)
    only_width = fit_peaks(res.spectrum, first, last, [c + 0.3], w, FitSettings(fixed_width=True))
    assert only_width.peaks[0].fwhm_unc == 0.0 and only_width.peaks[0].centroid_unc > 0.0
    assert abs(only_width.peaks[0].centroid - c) < 4 * only_width.peaks[0].centroid_unc


def test_fixing_the_shape_removes_the_bias_of_marginal_peaks() -> None:
    # 300 counts on 400 counts/channel (about 3.5 sigma). Measured with 1000 trials:
    # free shape +6.75 % +- 1.25 %, 1-sigma coverage 0.62; fixed shape +0.13 % +- 0.76 %, 0.68.
    n = 400
    fixed = run_fit_study(FitStudyCase("fixed", 300.0, 400.0, 6.0, fixed_shape=True), n)
    a = fixed.area
    assert fixed.failure_rate <= 0.01
    assert abs(a.bias) <= 5 * a.bias_se
    assert abs(a.pull_mean) <= 5 / math.sqrt(n) and abs(a.pull_sd - 1) <= 5 / math.sqrt(2 * n)
    assert abs(a.coverage_1 - 0.6827) <= 5 * math.sqrt(0.6827 * 0.3173 / n)
    assert math.isnan(fixed.centroid.bias) and math.isnan(fixed.sigma.bias)  # not estimated
    free = run_fit_study(FitStudyCase("free", 300.0, 400.0, 6.0), n)
    assert free.area.sd > a.sd  # the free shape also costs precision
