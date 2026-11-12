"""Property-based tests (Hypothesis) of calculations whose laws are known exactly (R4).

Each property is a mathematical identity of the quantity, not a re-implementation of the
code under test.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from nsw.analysis.background import subtract_background
from nsw.analysis.decay import decay_factor
from nsw.analysis.distributions import chi2_sf, normal_sf, poisson_pmf
from nsw.analysis.energy_calibration import fit_energy_calibration
from nsw.core.calibration import CalibrationPoint
from tests.conftest import make_raw, make_source

finite = {"allow_nan": False, "allow_infinity": False}
half_lives = st.floats(min_value=1.0, max_value=1e12, **finite)
durations = st.floats(min_value=0.0, max_value=1e9, **finite)


@given(half_lives, durations, durations)
def test_decay_factors_compose(t_half: float, a: float, b: float) -> None:
    """D(a)·D(b) = D(a + b): decay over consecutive intervals multiplies."""
    combined = decay_factor(t_half, a) * decay_factor(t_half, b)
    assert combined == pytest.approx(decay_factor(t_half, a + b), rel=1e-12, abs=1e-300)


@given(half_lives, st.integers(min_value=0, max_value=20))
def test_decay_factor_halves_every_half_life(t_half: float, n: int) -> None:
    assert decay_factor(t_half, n * t_half) == pytest.approx(0.5**n, rel=1e-12)


counts_arrays = st.lists(st.integers(min_value=0, max_value=10**6), min_size=4, max_size=64)
live_times = st.floats(min_value=1.0, max_value=1e6, **finite)


@settings(max_examples=60, deadline=None)
@given(counts_arrays, st.data(), live_times, live_times)
def test_background_subtraction_is_exact_and_keeps_negatives(
    g: list[int], data: st.DataObject, t_g: float, t_b: float
) -> None:
    """Net = G − αB with variance G + α²B, α = t_G/t_B; negative values are kept."""
    b = data.draw(st.lists(st.integers(0, 10**6), min_size=len(g), max_size=len(g)))
    assume(b != g)  # identical data are one acquisition and are refused by design
    common: dict[str, Any] = {"real_time_s": None}
    gross = make_raw(g, live_time_s=t_g, source=make_source("g.csv"), **common)
    bg = make_raw(b, live_time_s=t_b, source=make_source("b.csv"), **common)
    result = subtract_background(gross, bg.designated_as_background())
    alpha = t_g / t_b
    expected = np.asarray(g, dtype=np.float64) - alpha * np.asarray(b, dtype=np.float64)
    np.testing.assert_allclose(result.net.counts, expected, rtol=1e-12, atol=1e-9)
    variance = np.asarray(g, dtype=np.float64) + alpha**2 * np.asarray(b, dtype=np.float64)
    np.testing.assert_allclose(result.net.variance_array(), variance, rtol=1e-12, atol=1e-9)


@settings(max_examples=60, deadline=None)
@given(
    gain=st.floats(min_value=0.05, max_value=5.0, **finite),
    offset=st.floats(min_value=-50.0, max_value=50.0, **finite),
    channels=st.lists(
        st.floats(min_value=10.0, max_value=16000.0, **finite), min_size=3, max_size=8, unique=True
    )
    # distinct peaks (≥ 1 channel apart; nearly equal channels are refused by design)
    .filter(lambda c: min(b - a for a, b in zip(sorted(c), sorted(c)[1:], strict=False)) >= 1.0)
    .filter(lambda c: max(c) - min(c) > 50.0),
)
def test_linear_calibration_recovers_exact_points(
    gain: float, offset: float, channels: list[float]
) -> None:
    """Points lying exactly on E = offset + gain·ch are reproduced at every channel."""
    points = [
        CalibrationPoint(
            channel=c,
            channel_uncertainty=0.05,
            reference_value=offset + gain * c,
            reference_uncertainty=0.01,
            reference_source="exact synthetic line",
        )
        for c in channels
    ]
    fit = fit_energy_calibration(points, order=1)
    probe = np.array([min(channels), 0.5 * (min(channels) + max(channels)), max(channels)])
    energy, _, _ = fit.energy(probe)
    np.testing.assert_allclose(energy, offset + gain * probe, rtol=1e-9, atol=1e-7)


@given(
    st.floats(min_value=0.0, max_value=1e4, **finite),
    st.floats(min_value=0.0, max_value=1e4, **finite),
    st.integers(min_value=1, max_value=2000),
)
def test_chi2_sf_is_a_decreasing_probability(x1: float, x2: float, dof: int) -> None:
    lo, hi = sorted((x1, x2))
    p_lo, p_hi = chi2_sf(lo, dof), chi2_sf(hi, dof)
    assert 0.0 <= p_hi <= p_lo <= 1.0


@given(st.floats(min_value=-30.0, max_value=30.0, **finite))
def test_normal_sf_symmetry(z: float) -> None:
    """Φ(−z) + Φ(z) = 1."""
    assert normal_sf(z) + normal_sf(-z) == pytest.approx(1.0, rel=1e-14)


@given(st.floats(min_value=1e-6, max_value=100.0, **finite))
def test_poisson_pmf_is_normalised_with_correct_mean(mean: float) -> None:
    n = np.arange(0.0, 400.0)
    p = poisson_pmf(n, mean)
    assert math.fsum(p) == pytest.approx(1.0, rel=1e-12)
    assert math.fsum(n * p) == pytest.approx(mean, rel=1e-10)
