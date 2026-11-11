"""Activity of a source from fitted peaks and a full-energy-peak efficiency curve.

One line
--------
    A_start = N / (eps(E) P_gamma t_live f(t_real))       activity at the start of the
                                                          measurement
    A_ref   = A_start / D(t_start - t_ref)                activity at the reference time

with f and D from ``nsw.analysis.decay``. The reference time must be stated; the
default is the start of the measurement. The live time is used once: no second
dead-time correction is applied. Decay during the measurement is included through f,
which matters only when the half-life is not long against the real time.

Relative uncertainty groups:
    statistical   u(N)/N
    systematic    emission probability, efficiency curve at E, half-life

Several lines of one nuclide
----------------------------
The line results are not independent: they share the efficiency curve and the
half-life. They are combined in the logarithm by generalised least squares with

    V_ij = delta_ij (stat_i^2 + line_i^2) + C_eps(E_i, E_j) + h_i h_j u(T)^2,

C_eps the relative covariance of the curve between the two energies and h the
half-life sensitivity. The chi-square of the lines about their mean (ndf = lines - 1)
is a consistency test: a failure points at an interfering peak, a wrong emission
probability, coincidence summing, or a wrong nuclide.

The efficiency curve must be an *absolute* curve measured in the same geometry as the
sample. That equality cannot be checked here and is recorded as an assumption.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.stats import chi2 as chi2_distribution

from nsw.analysis import decay
from nsw.analysis.efficiency import EfficiencyCurve, GammaLine, MeasurementTiming
from nsw.core.errors import ProcessingError
from nsw.core.units import Quantity, Unit

POOR_FIT_P = 1e-3
ASSUMPTION = (
    "Assumes the sample was measured in the same source-detector geometry as the "
    "efficiency calibration, without coincidence-summing or self-attenuation differences."
)


@dataclass(frozen=True, slots=True)
class LineActivity:
    line: GammaLine
    activity_bq: float  # at the reference time
    rel_stat: float
    rel_line: float
    rel_efficiency: float
    half_life_sensitivity: float  # d ln A / dT
    extrapolated: bool  # efficiency taken outside the calibrated energy range

    def quantity(self, half_life_unc_s: float) -> Quantity:
        rel_sys = math.sqrt(
            self.rel_line**2
            + self.rel_efficiency**2
            + (self.half_life_sensitivity * half_life_unc_s) ** 2
        )
        return Quantity(
            self.activity_bq,
            Unit.BECQUEREL,
            stat=self.activity_bq * self.rel_stat,
            sys=self.activity_bq * rel_sys,
        )


@dataclass(frozen=True)
class ActivityResult:
    activity: Quantity  # combined, at ``reference_time``
    reference_time: datetime
    lines: tuple[LineActivity, ...]
    weights: tuple[float, ...]
    chi2: float
    ndf: int
    p_value: float  # NaN for a single line
    warnings: tuple[str, ...]

    @property
    def consistent(self) -> bool:
        return self.ndf == 0 or self.p_value >= POOR_FIT_P


def line_activity(
    net_area: float,
    net_area_unc: float,
    line: GammaLine,
    curve: EfficiencyCurve,
    timing: MeasurementTiming,
    half_life_s: float,
    reference_time: datetime | None = None,
) -> LineActivity:
    """Activity at ``reference_time`` (default: start of the measurement) from one line.

    A negative or zero net area is accepted (it is an unbiased estimate of a small or
    absent activity) as long as its uncertainty is positive.
    """
    if not curve.absolute:
        raise ProcessingError("an activity needs an absolute efficiency curve, not a relative one")
    if not (math.isfinite(net_area) and math.isfinite(net_area_unc) and net_area_unc > 0.0):
        raise ProcessingError("the net peak area needs a finite value and a positive uncertainty")
    reference = reference_time if reference_time is not None else timing.start_time
    dt = decay.elapsed_seconds(reference, timing.start_time)
    d = decay.decay_factor(half_life_s, dt)
    f = decay.in_measurement_factor(half_life_s, timing.real_time_s)
    eps, rel_eps, extrapolated = curve.efficiency(line.energy_kev)
    value = net_area / (float(eps[0]) * line.emission_probability * timing.live_time_s * f * d)
    sensitivity = -(
        decay.d_ln_decay_factor_d_half_life(half_life_s, dt)
        + decay.d_ln_in_measurement_factor_d_half_life(half_life_s, timing.real_time_s)
    )
    return LineActivity(
        line=line,
        activity_bq=value,
        rel_stat=net_area_unc / abs(net_area) if net_area != 0.0 else math.inf,
        rel_line=line.emission_probability_unc / line.emission_probability,
        rel_efficiency=float(rel_eps[0]),
        half_life_sensitivity=sensitivity,
        extrapolated=bool(extrapolated[0]),
    )


def combine_lines(
    lines: Sequence[LineActivity],
    curve: EfficiencyCurve,
    half_life_unc_s: float,
    reference_time: datetime,
) -> ActivityResult:
    """Combine the lines of one nuclide, with their correlations (see module docstring)."""
    if not lines:
        raise ProcessingError("no line results to combine")
    if any(r.activity_bq <= 0.0 or not math.isfinite(r.rel_stat) for r in lines):
        raise ProcessingError(
            "lines with a zero or negative activity estimate cannot be combined in the "
            "logarithm; report them individually (they are non-detections)"
        )
    n = len(lines)
    y = np.log([r.activity_bq for r in lines])
    stat = np.array([r.rel_stat for r in lines])
    v_stat = np.diag(stat**2)
    v = v_stat + np.diag([r.rel_line**2 for r in lines])
    v = v + curve.relative_covariance([r.line.energy_kev for r in lines])
    h = np.array([r.half_life_sensitivity for r in lines]) * half_life_unc_s
    v = v + np.outer(h, h)
    v_inv = np.linalg.inv(v)
    ones = np.ones(n)
    norm = float(ones @ v_inv @ ones)
    weights = (v_inv @ ones) / norm
    mean = float(weights @ y)
    residuals = y - mean
    chi2 = float(residuals @ v_inv @ residuals)
    ndf = n - 1
    p_value = float(chi2_distribution.sf(chi2, ndf)) if ndf > 0 else math.nan
    rel_total = math.sqrt(1.0 / norm)
    rel_stat = math.sqrt(float(weights @ v_stat @ weights))
    rel_sys = math.sqrt(max(rel_total**2 - rel_stat**2, 0.0))
    value = math.exp(mean)
    warnings = [ASSUMPTION, *curve.warnings]
    if any(r.extrapolated for r in lines):
        warnings.append(
            "at least one line lies outside the calibrated energy range: the efficiency "
            "there is extrapolated and its stated uncertainty does not cover model error"
        )
    if ndf > 0 and p_value < POOR_FIT_P:
        warnings.append(
            f"the lines are not consistent with one activity (chi-square {chi2:.1f} for "
            f"{ndf} degrees of freedom): check for interfering peaks, coincidence summing, "
            "the emission probabilities and the nuclide assignment"
        )
    return ActivityResult(
        activity=Quantity(value, Unit.BECQUEREL, stat=value * rel_stat, sys=value * rel_sys),
        reference_time=reference_time,
        lines=tuple(lines),
        weights=tuple(float(w) for w in weights),
        chi2=chi2,
        ndf=ndf,
        p_value=p_value,
        warnings=tuple(warnings),
    )


def specific_activity(
    activity: Quantity, mass_kg: float, mass_unc_kg: float = 0.0
) -> tuple[float, float, float]:
    """Activity per kilogram: (value, statistical, systematic) in Bq/kg. The mass
    uncertainty is added to the systematic part in quadrature."""
    if not (math.isfinite(mass_kg) and mass_kg > 0.0):
        raise ProcessingError("a sample mass must be positive")
    if not (math.isfinite(mass_unc_kg) and mass_unc_kg >= 0.0):
        raise ProcessingError("the mass uncertainty must be >= 0")
    a = activity.to(Unit.BECQUEREL)
    value = a.value / mass_kg
    sys = math.hypot(a.sys / mass_kg, value * mass_unc_kg / mass_kg)
    return value, a.stat / mass_kg, sys
