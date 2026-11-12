"""The special-function forms in nsw.analysis.distributions equal scipy.stats (R1).

scipy.stats is the independent reference here; it is imported only by this test.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from nsw.analysis.distributions import chi2_sf, normal_sf, poisson_pmf

RTOL = 1e-12

CHI2_X = [0.0, 1e-12, 1e-6, 0.01, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 100.0, 300.0, 1e3, 5e3]
CHI2_DOF = [1, 2, 3, 5, 10, 50, 100, 1000, 0.5, 2.5]


@pytest.mark.parametrize("dof", CHI2_DOF)
def test_chi2_sf_matches_scipy_stats(dof: float) -> None:
    for x in CHI2_X + [dof * f for f in (0.1, 0.9, 1.0, 1.1, 3.0, 10.0)]:
        expected = float(stats.chi2.sf(x, dof))
        got = chi2_sf(x, dof)
        if expected == 0.0:
            assert got == 0.0 or got < 1e-300
        else:
            assert got == pytest.approx(expected, rel=RTOL), (x, dof)


def test_chi2_sf_edge_cases() -> None:
    assert chi2_sf(-1.0, 3) == float(stats.chi2.sf(-1.0, 3)) == 1.0
    assert math.isnan(chi2_sf(math.nan, 3))
    assert chi2_sf(math.inf, 3) == float(stats.chi2.sf(math.inf, 3)) == 0.0


@pytest.mark.parametrize(
    "z", [-40.0, -8.0, -3.0, -1.0, -1e-9, 0.0, 1e-9, 0.5, 1.0, 3.0, 5.0, 8.0, 20.0, 37.0]
)
def test_normal_sf_matches_scipy_stats(z: float) -> None:
    assert normal_sf(z) == pytest.approx(float(stats.norm.sf(z)), rel=RTOL)


def test_normal_sf_edge_cases() -> None:
    assert normal_sf(math.inf) == 0.0
    assert normal_sf(-math.inf) == 1.0
    assert math.isnan(normal_sf(math.nan))


def test_poisson_pmf_matches_scipy_stats() -> None:
    n = np.arange(0.0, 241.0)[None, :]
    means = np.array([1e-300, 1e-12, 1e-6, 0.01, 0.3, 1.0, 2.5, 10.0, 47.3, 100.0, 200.0])
    means = means[:, None]
    expected = stats.poisson.pmf(n, means)
    got = poisson_pmf(n, means)
    big = expected > 1e-280
    np.testing.assert_allclose(got[big], expected[big], rtol=1e-10)
    assert np.all(got[~big] < 1e-270)


def test_poisson_pmf_zero_mean() -> None:
    assert poisson_pmf(0.0, 0.0) == pytest.approx(1.0)
    assert poisson_pmf(3.0, 0.0) == 0.0
