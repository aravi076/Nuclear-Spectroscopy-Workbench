"""Limits from a fit: analytic cross-check and Monte Carlo error rates (synthetic only)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nsw.analysis.fitting import BackgroundShape, FitSettings, fit_peaks
from nsw.analysis.limits import LimitMethod, LimitSettings, gaussian_bins, poisson_u_tilde
from nsw.services import roi as rs
from tests.conftest import make_raw

N_CH, CENTRE, SIGMA, CONTINUUM = 41, 21.0, 2.0, 60.0
FWHM = SIGMA * 2.3548200450309493


def _x() -> np.ndarray:
    return np.arange(1, N_CH + 1, dtype=np.float64)


def test_fit_u_tilde_matches_the_counting_cross_check() -> None:
    """Flat continuum, known continuum (no nuisance): ũ²(Ñ) = 1/Σ g²/(B + Ñ g); at Ñ = 0
    this is B / Σ g² for a constant B."""
    x = _x()
    g = gaussian_bins(x, CENTRE, SIGMA)
    u = poisson_u_tilde(np.full(N_CH, CONTINUUM), g)
    assert u(0.0) == pytest.approx(math.sqrt(CONTINUUM / float(np.sum(g * g))), rel=1e-12)
    expected = 1.0 / math.sqrt(float(np.sum(g * g / (CONTINUUM + 500.0 * g))))
    assert u(500.0) == pytest.approx(expected, rel=1e-12)
    # free continuum level costs information: larger ũ(0)
    u_free = poisson_u_tilde(np.full(N_CH, CONTINUUM), g, np.ones((1, N_CH)))
    assert u_free(0.0) > u(0.0)


def _fit(counts: np.ndarray):  # type: ignore[no-untyped-def]
    s = make_raw(counts)
    settings = FitSettings(
        method="poisson",
        background=BackgroundShape.LINEAR,
        fixed_centroids=True,
        fixed_width=True,
    )
    return fit_peaks(s, 1, N_CH, [CENTRE], FWHM, settings)


@pytest.mark.slow
@pytest.mark.parametrize("method", [LimitMethod.ISO_11929, LimitMethod.CURRIE])
def test_monte_carlo_rates_of_fit_based_limits(method: LimitMethod) -> None:
    rng = np.random.default_rng(20261112)
    x = _x()
    g = gaussian_bins(x, CENTRE, SIGMA)
    settings = LimitSettings(method=method)
    reference = _fit(np.rint(np.full(N_CH, CONTINUUM)).astype(int))
    lim = rs.fit_limits(reference, 0, settings)
    assert lim.detection_limit is not None
    trials = 3000
    above = sum(
        _fit(rng.poisson(np.full(N_CH, CONTINUUM))).peaks[0].area > lim.decision_threshold
        for _ in range(trials)
    )
    detected = sum(
        _fit(rng.poisson(CONTINUUM + lim.detection_limit * g)).peaks[0].area
        > lim.decision_threshold
        for _ in range(trials)
    )
    se = math.sqrt(0.05 * 0.95 / trials)
    assert abs(above / trials - 0.05) < 5 * se, above / trials
    assert abs(detected / trials - 0.95) < 5 * se, detected / trials
