"""HPGe low-energy tail model: shape, parameter recovery, coverage, model selection.

Synthetic ground truth only. The shape is checked against direct numerical integration
of the EMG density (independent of the closed-form CDF used by the model).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.integrate import quad

from nsw.analysis.fitting import BackgroundShape, FitMethod, FitSettings, fit_peaks
from nsw.analysis.tailfit import fit_tailed, peak_shape, tail_test
from tests.conftest import make_raw

MU, SIGMA, ETA, BETA = 100.0, 2.0, 0.15, 4.0
FWHM = SIGMA * 2.3548200450309493
X = np.arange(60, 141, dtype=np.float64)
CONT = 40.0


def emg_low_density(x: float, mu: float, sigma: float, beta: float) -> float:
    """Low-side EMG density by direct convolution (independent reference)."""

    def integrand(e: float) -> float:
        return (
            math.exp(-e / beta)
            / beta
            * math.exp(-0.5 * ((x + e - mu) / sigma) ** 2)
            / (sigma * math.sqrt(2 * math.pi))
        )

    return quad(integrand, 0.0, math.inf, limit=200)[0]


def test_shape_matches_numerical_integration_and_is_normalised() -> None:
    x = np.array([90.0, 96.0, 99.0, 100.0, 101.0, 104.0])
    got = peak_shape(x, MU, SIGMA, 1.0, BETA)  # pure tail
    for xi, gi in zip(x, got, strict=True):
        ref = quad(lambda t: emg_low_density(t, MU, SIGMA, BETA), xi - 0.5, xi + 0.5)[0]
        assert gi == pytest.approx(ref, rel=1e-6, abs=1e-12)
    wide = np.arange(-200.0, 300.0)
    assert float(peak_shape(wide, MU, SIGMA, ETA, BETA).sum()) == pytest.approx(1.0, abs=1e-9)
    gauss_only = peak_shape(X, MU, SIGMA, 0.0, BETA)
    assert float(gauss_only.sum()) == pytest.approx(1.0, abs=1e-9)


def expectation(area: float, eta: float = ETA) -> np.ndarray:
    return CONT + area * peak_shape(X, MU, SIGMA, eta, BETA)


def spectrum_of(counts: np.ndarray):  # type: ignore[no-untyped-def]
    full = np.zeros(200, dtype=np.int64)
    full[59:140] = counts
    return make_raw(full)


def gaussian_fit(s):  # type: ignore[no-untyped-def]
    settings = FitSettings(method=FitMethod.POISSON, background=BackgroundShape.LINEAR)
    return fit_peaks(s, 60, 140, [MU], FWHM, settings)


def test_recovers_tail_parameters_and_area_where_the_gaussian_is_biased() -> None:
    rng = np.random.default_rng(3)
    area = 2e5
    s = spectrum_of(rng.poisson(expectation(area)))
    tail = fit_tailed(s, 60, 140, [MU], FWHM)
    assert tail.converged
    assert abs(tail.areas[0] - area) < 5 * tail.area_uncs[0]
    assert abs(tail.eta - ETA) < 5 * tail.eta_unc
    assert abs(tail.beta - BETA) < 5 * tail.beta_unc
    gauss = gaussian_fit(s)
    gauss_bias = abs(gauss.peaks[0].area - area) / gauss.peaks[0].area_unc
    tail_bias = abs(tail.areas[0] - area) / tail.area_uncs[0]
    assert gauss_bias > 5 and tail_bias < 5  # the documented problem, and its fix


@pytest.mark.slow
def test_area_pulls_monte_carlo() -> None:
    rng = np.random.default_rng(11)
    area = 5e4
    pulls = []
    for _ in range(150):
        tail = fit_tailed(spectrum_of(rng.poisson(expectation(area))), 60, 140, [MU], FWHM)
        if tail.converged and tail.area_uncs[0] > 0:
            pulls.append((tail.areas[0] - area) / tail.area_uncs[0])
    p = np.asarray(pulls)
    assert p.size >= 140
    assert abs(p.mean()) < 5 / math.sqrt(p.size)
    assert 0.8 < p.std() < 1.25


@pytest.mark.slow
def test_bootstrap_selection_prefers_the_right_model() -> None:
    rng = np.random.default_rng(5)
    for eta, expected in ((0.0, "gaussian"), (0.2, "tailed")):
        s = spectrum_of(rng.poisson(expectation(1e5, eta)))
        g = gaussian_fit(s)
        test = tail_test(
            s, 60, 140, g.fitted, g.statistic, [MU], FWHM, [g.peaks[0].area], replicas=60
        )
        assert test.preferred == expected, test.reason
