"""Decision thresholds and detection limits: closed forms and Monte Carlo rates."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nsw.analysis.limits import (
    CountingModel,
    LimitMethod,
    LimitSettings,
    characteristic_limits,
    coverage_interval,
    currie_limits,
    iso_limits,
)
from nsw.core.errors import ProcessingError

K = 1.6448536269514722  # standard normal 95 % quantile (independent constant)


def test_currie_reproduces_the_classical_formula() -> None:
    for b in (0.0, 1.0, 10.0, 100.0, 1e4):
        lim = currie_limits(CountingModel(b, b), LimitSettings(method=LimitMethod.CURRIE))
        assert lim.decision_threshold == pytest.approx(K * math.sqrt(2 * b), rel=1e-12, abs=1e-12)
        assert lim.detection_limit == pytest.approx(K**2 + 2 * K * math.sqrt(2 * b), rel=1e-12)
        assert lim.detection_limit == pytest.approx(2.706 + 4.653 * math.sqrt(b), rel=1e-3)


@pytest.mark.parametrize("b,ub2", [(0.0, 0.0), (5.0, 5.0), (100.0, 25.0), (1e4, 1e3)])
def test_iso_equals_closed_form_without_w_uncertainty(b: float, ub2: float) -> None:
    """With α = β and u(w) = 0: y* = k σ0 and y# = 2 y* + k² (same as Currie)."""
    model = CountingModel(b, ub2)
    iso = characteristic_limits(model, LimitSettings())
    cur = characteristic_limits(model, LimitSettings(method="currie"))
    s0 = math.sqrt(b + ub2)
    assert iso.method is LimitMethod.ISO_11929
    assert iso.decision_threshold == pytest.approx(K * s0, rel=1e-12, abs=1e-12)
    assert iso.detection_limit == pytest.approx(2 * K * s0 + K**2, rel=1e-10)
    assert cur.detection_limit == pytest.approx(iso.detection_limit, rel=1e-10)


def test_iso_with_conversion_factor_uncertainty() -> None:
    """Closed form for α = β: y# = (2 y* + k² w) / (1 − k² u_rel²)."""
    w, urel, b = 0.37, 0.08, 400.0
    model = CountingModel(b, b, w=w, w_rel_uncertainty=urel)
    lim = characteristic_limits(model, LimitSettings())
    y_star = K * w * math.sqrt(2 * b)
    assert lim.decision_threshold == pytest.approx(y_star, rel=1e-12)
    assert lim.detection_limit == pytest.approx(
        (2 * y_star + K**2 * w) / (1 - K**2 * urel**2), rel=1e-9
    )
    currie = characteristic_limits(model, LimitSettings(method="currie"))
    assert currie.detection_limit is not None and currie.detection_limit < lim.detection_limit
    assert "not included" in currie.note


def test_detection_limit_does_not_exist_for_large_w_uncertainty() -> None:
    model = CountingModel(10.0, 10.0, w=2.0, w_rel_uncertainty=0.7)
    lim = characteristic_limits(model, LimitSettings())
    assert lim.detection_limit is None and "does not exist" in lim.note


def test_unequal_alpha_beta() -> None:
    s = LimitSettings(alpha=0.01, beta=0.1)
    model = CountingModel(50.0, 50.0)
    iso = iso_limits(model.u_tilde, s)
    cur = currie_limits(model, s)
    assert iso.detection_limit == pytest.approx(cur.detection_limit, rel=1e-10)
    assert iso.decision_threshold == pytest.approx(2.3263478740408408 * 10.0, rel=1e-12)


def test_settings_validation() -> None:
    with pytest.raises(ProcessingError):
        LimitSettings(alpha=0.5)
    with pytest.raises(ProcessingError):
        LimitSettings(method="bayes")  # type: ignore[arg-type]
    with pytest.raises(ProcessingError):
        CountingModel(-1.0, 0.0)


def test_coverage_interval_limits() -> None:
    big = coverage_interval(1000.0, 10.0)
    assert big.best_estimate == pytest.approx(1000.0, rel=1e-12)
    assert big.lower == pytest.approx(1000.0 - 1.959963984540054 * 10.0, rel=1e-9)
    assert big.upper == pytest.approx(1000.0 + 1.959963984540054 * 10.0, rel=1e-9)
    for y in (-20.0, -5.0, 0.0, 3.0, 10.0):
        ci = coverage_interval(y, 5.0)
        assert 0.0 <= ci.lower < ci.best_estimate < ci.upper
        assert ci.best_estimate > max(y, 0.0)


@pytest.mark.parametrize("method", [LimitMethod.ISO_11929, LimitMethod.CURRIE])
def test_monte_carlo_error_rates(method: LimitMethod) -> None:
    """Paired blank counting: false positives ≈ α at zero, detections ≈ 1 − β at y#."""
    rng = np.random.default_rng(20261112)
    b, trials = 2000.0, 400_000
    lim = characteristic_limits(CountingModel(b, b), LimitSettings(method=method))
    assert lim.detection_limit is not None
    gross0 = rng.poisson(b, trials) - rng.poisson(b, trials)
    rate = float(np.mean(gross0 > lim.decision_threshold))
    se = math.sqrt(0.05 * 0.95 / trials)
    assert abs(rate - 0.05) < 5 * se + 0.002  # 0.002: discreteness of the counts
    gross = rng.poisson(b + lim.detection_limit, trials) - rng.poisson(b, trials)
    detected = float(np.mean(gross > lim.decision_threshold))
    assert abs(detected - 0.95) < 5 * se + 0.002
