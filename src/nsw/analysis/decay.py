"""Radioactive-decay factors with first-order uncertainty propagation.

Definitions (half-life T, decay constant lambda = ln 2 / T)
-----------------------------------------------------------
decay factor         D(dt) = exp(-lambda dt): activity at time t0 + dt relative to the
                     activity at t0. dt may be negative (looking back in time).
in-measurement       f(t) = (1 - exp(-lambda t)) / (lambda t): mean activity during a
factor               measurement of duration t relative to the activity at its start
                     (f -> 1 for lambda t -> 0). t is the *real* (clock) time: decay does
                     not pause while the spectrometer is busy.

Derivatives with respect to T (used for the uncertainty of the half-life):

    d ln D / dT = lambda dt / T
    d ln f / dT = -(lambda / T) [ t exp(-lambda t) / (1 - exp(-lambda t)) - 1 / lambda ]

Single-nuclide decay only: no ingrowth of daughters, no decay chains.
"""

from __future__ import annotations

import math
from datetime import datetime

from nsw.core.errors import ProcessingError

LN2 = math.log(2.0)


def decay_constant(half_life_s: float) -> float:
    if not math.isfinite(half_life_s) or half_life_s <= 0.0:
        raise ProcessingError("a half-life must be a positive, finite number of seconds")
    return LN2 / half_life_s


def elapsed_seconds(reference: datetime, later: datetime) -> float:
    """later - reference in seconds. Both must be timezone-aware or both naive."""
    if (reference.tzinfo is None) != (later.tzinfo is None):
        raise ProcessingError(
            "cannot subtract a timezone-aware time from a naive one; give both times in "
            "the same way"
        )
    return (later - reference).total_seconds()


def decay_factor(half_life_s: float, dt_s: float) -> float:
    return math.exp(-decay_constant(half_life_s) * dt_s)


def d_ln_decay_factor_d_half_life(half_life_s: float, dt_s: float) -> float:
    return decay_constant(half_life_s) * dt_s / half_life_s


def in_measurement_factor(half_life_s: float, real_time_s: float) -> float:
    if not math.isfinite(real_time_s) or real_time_s <= 0.0:
        raise ProcessingError("the measurement duration must be positive")
    x = decay_constant(half_life_s) * real_time_s
    return -math.expm1(-x) / x


def d_ln_in_measurement_factor_d_half_life(half_life_s: float, real_time_s: float) -> float:
    lam = decay_constant(half_life_s)
    x = lam * real_time_s
    # t e^{-x} / (1 - e^{-x}) - 1/lambda = (1/lambda) [x / expm1(x) - 1]
    bracket = (x / math.expm1(x) - 1.0) / lam
    return -(lam / half_life_s) * bracket
