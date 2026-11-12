"""Full-energy-peak efficiency: points from reference sources and an efficiency curve.

Terminology (kept distinct in the data model)
---------------------------------------------
absolute full-energy-peak efficiency
    counts in the full-energy peak per photon of that energy *emitted by the source*,
    for one fixed source-detector geometry. This is what a reference source of known
    activity measures, and what this module computes.
geometric efficiency
    fraction of emitted photons that reach the detector (solid angle, absorbers).
intrinsic full-energy-peak efficiency
    counts in the peak per photon *incident on the detector*. absolute = geometric x
    intrinsic (for a point source without attenuation). Separating the two needs the
    geometry, which this module never assumes: the geometry is a recorded description,
    and a curve is valid only for the geometry it was measured in.
relative efficiency
    the energy dependence only, in arbitrary units, from lines of known relative
    emission probability; no activity needed (:func:`relative_points`).

Efficiency from one line of a reference source
----------------------------------------------
    N = eps P_gamma A_0 D(dt) f(t_real) t_live
    eps = N / (P_gamma A_0 D f t_live)

N net peak area, P_gamma photons per decay, A_0 activity at the reference time, D decay
from the reference time to the start of the measurement, f mean activity during the
measurement relative to its start, t_live live time. The live time is used once: the
instrument's dead-time correction is not applied again anywhere. Assumption: counting
losses are spread evenly over the measurement (true when the count rate is constant,
i.e. when the half-life is long against the measurement).

Relative standard uncertainties are kept in three groups:
    statistical         u(N)/N of the peak fit
    line                u(P_gamma)/P_gamma, independent between lines
    source              activity and half-life: common to all lines of one source,
                        hence fully correlated between them

Efficiency curve
----------------
    ln eps(E) = sum_{k=0}^{order} a_k L^k,   L = ln(E / E_0),

E_0 the geometric mean of the calibration energies. Generalised least squares in the
logarithm with the full covariance of the points, V_ij = delta_ij (stat_i^2 + line_i^2)
+ [same source] source_i source_j, so a wrong activity moves all points of that source
together instead of being averaged away. Coefficient covariance (X^T V^-1 X)^-1;
relative uncertainty of eps(E): sqrt(g^T C g), g = (1, L, L^2, ...).

Not corrected here, and stated with every result: true coincidence summing (cascade
emitters at close geometry), self-attenuation in extended sources, and any difference
between the calibration geometry and the measurement geometry.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from nsw.analysis import decay
from nsw.analysis.distributions import chi2_sf
from nsw.core.errors import ProcessingError

POOR_FIT_P = 1e-3
MAX_ORDER = 4
Float = NDArray[np.float64]
UNCORRECTED = (
    "true coincidence summing is not corrected",
    "self-attenuation in the source is not corrected",
    "valid only for the source-detector geometry of the calibration measurement",
)


@dataclass(frozen=True, slots=True)
class GammaLine:
    """One gamma line of a nuclide. Values need a recorded origin (``reference``)."""

    energy_kev: float
    emission_probability: float  # photons per decay
    emission_probability_unc: float
    reference: str  # where energy and emission probability come from

    def __post_init__(self) -> None:
        if not self.reference.strip():
            raise ProcessingError("a gamma line needs the source of its nuclear data")
        if not (math.isfinite(self.energy_kev) and self.energy_kev > 0.0):
            raise ProcessingError("line energy must be positive")
        if not 0.0 < self.emission_probability <= 2.0:
            raise ProcessingError("emission probability must be in (0, 2] photons per decay")
        if not (
            math.isfinite(self.emission_probability_unc) and self.emission_probability_unc >= 0.0
        ):
            raise ProcessingError("emission-probability uncertainty must be >= 0")


@dataclass(frozen=True, slots=True)
class ReferenceSource:
    """A source of certified (or, for tests, assumed synthetic) activity."""

    name: str
    activity_bq: float  # at ``reference_time``
    activity_unc_bq: float  # one standard uncertainty
    reference_time: datetime
    half_life_s: float
    half_life_unc_s: float
    provenance: str  # certificate / origin of activity and half-life
    synthetic: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.provenance.strip():
            raise ProcessingError("a reference source needs a name and a provenance")
        if not (math.isfinite(self.activity_bq) and self.activity_bq > 0.0):
            raise ProcessingError("source activity must be positive")
        if not (math.isfinite(self.activity_unc_bq) and self.activity_unc_bq >= 0.0):
            raise ProcessingError("activity uncertainty must be >= 0")
        decay.decay_constant(self.half_life_s)
        if not (math.isfinite(self.half_life_unc_s) and self.half_life_unc_s >= 0.0):
            raise ProcessingError("half-life uncertainty must be >= 0")


@dataclass(frozen=True, slots=True)
class MeasurementTiming:
    start_time: datetime
    live_time_s: float
    real_time_s: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.live_time_s) and self.live_time_s > 0.0):
            raise ProcessingError("live time must be positive")
        if not (
            math.isfinite(self.real_time_s) and self.real_time_s >= self.live_time_s * 0.999999
        ):
            raise ProcessingError("real time must be at least the live time")


@dataclass(frozen=True, slots=True)
class EfficiencyPoint:
    energy_kev: float
    efficiency: float
    rel_stat: float
    rel_line: float
    rel_source: float  # correlated between points with the same ``source``
    source: str
    absolute: bool = True

    @property
    def rel_total(self) -> float:
        return math.sqrt(self.rel_stat**2 + self.rel_line**2 + self.rel_source**2)

    @property
    def uncertainty(self) -> float:
        return self.efficiency * self.rel_total


def decay_terms(source: ReferenceSource, timing: MeasurementTiming) -> tuple[float, float, float]:
    """(D, f, d ln(D f)/dT) for a measurement of ``source``."""
    dt = decay.elapsed_seconds(source.reference_time, timing.start_time)
    d = decay.decay_factor(source.half_life_s, dt)
    f = decay.in_measurement_factor(source.half_life_s, timing.real_time_s)
    slope = decay.d_ln_decay_factor_d_half_life(
        source.half_life_s, dt
    ) + decay.d_ln_in_measurement_factor_d_half_life(source.half_life_s, timing.real_time_s)
    return d, f, slope


def efficiency_point(
    net_area: float,
    net_area_unc: float,
    line: GammaLine,
    source: ReferenceSource,
    timing: MeasurementTiming,
) -> EfficiencyPoint:
    """Absolute full-energy-peak efficiency from one fitted peak of a reference source."""
    if not (math.isfinite(net_area) and net_area > 0.0):
        raise ProcessingError("an efficiency needs a positive net peak area")
    if not (math.isfinite(net_area_unc) and net_area_unc > 0.0):
        raise ProcessingError("the net peak area needs a positive uncertainty")
    d, f, slope = decay_terms(source, timing)
    emitted = line.emission_probability * source.activity_bq * d * f * timing.live_time_s
    return EfficiencyPoint(
        energy_kev=line.energy_kev,
        efficiency=net_area / emitted,
        rel_stat=net_area_unc / net_area,
        rel_line=line.emission_probability_unc / line.emission_probability,
        rel_source=math.hypot(
            source.activity_unc_bq / source.activity_bq, slope * source.half_life_unc_s
        ),
        source=source.name,
    )


def relative_points(
    areas: Sequence[tuple[float, float]], lines: Sequence[GammaLine], label: str
) -> list[EfficiencyPoint]:
    """Relative efficiency N / P_gamma for lines of one nuclide in one spectrum, scaled so
    that the first point is 1. Arbitrary units: only ratios between energies mean
    anything. No activity, time or geometry enters."""
    if len(areas) != len(lines) or len(areas) < 2:
        raise ProcessingError("relative efficiency needs at least two lines with their areas")
    raw = []
    for (area, unc), line in zip(areas, lines, strict=True):
        if area <= 0.0 or unc <= 0.0:
            raise ProcessingError("relative efficiency needs positive areas with uncertainties")
        raw.append((line, area / line.emission_probability, unc / area))
    scale = raw[0][1]
    return [
        EfficiencyPoint(
            energy_kev=line.energy_kev,
            efficiency=value / scale,
            rel_stat=rel,
            rel_line=line.emission_probability_unc / line.emission_probability,
            rel_source=0.0,
            source=label,
            absolute=False,
        )
        for line, value, rel in raw
    ]


def point_covariance(points: Sequence[EfficiencyPoint]) -> Float:
    """Relative covariance matrix of the points (covariance of ln eps)."""
    n = len(points)
    v = np.zeros((n, n))
    for i, p in enumerate(points):
        v[i, i] = p.rel_stat**2 + p.rel_line**2
        for j, q in enumerate(points):
            if p.source == q.source:
                v[i, j] += p.rel_source * q.rel_source
    return v


@dataclass(frozen=True)
class EfficiencyCurve:
    order: int
    coefficients: Float  # a_k of ln eps = sum a_k ln(E/E0)^k
    covariance: Float
    reference_energy_kev: float  # E0
    points: tuple[EfficiencyPoint, ...]
    residuals: Float  # ln eps_i - model
    chi2: float
    ndf: int
    p_value: float
    absolute: bool
    geometry: str
    warnings: tuple[str, ...]

    @property
    def energy_range_kev(self) -> tuple[float, float]:
        energies = [p.energy_kev for p in self.points]
        return min(energies), max(energies)

    @property
    def poor_fit(self) -> bool:
        return self.ndf > 0 and self.p_value < POOR_FIT_P

    @property
    def verifiable(self) -> bool:
        return self.ndf >= 1 and not self.poor_fit

    def _basis(self, energy: Float) -> Float:
        log = np.log(energy / self.reference_energy_kev)
        return np.vstack([log**k for k in range(self.order + 1)])

    def efficiency(
        self, energy_kev: float | Sequence[float] | Float
    ) -> tuple[Float, Float, NDArray[np.bool_]]:
        """Efficiency, its *relative* standard uncertainty, and an extrapolation flag."""
        e = np.atleast_1d(np.asarray(energy_kev, dtype=np.float64))
        if np.any(e <= 0.0):
            raise ProcessingError("energies must be positive")
        basis = self._basis(e)
        value = np.exp(self.coefficients @ basis)
        rel = np.sqrt(np.maximum(np.einsum("ik,ij,jk->k", basis, self.covariance, basis), 0.0))
        lo, hi = self.energy_range_kev
        return (
            np.asarray(value, dtype=np.float64),
            np.asarray(rel, dtype=np.float64),
            np.asarray((e < lo) | (e > hi), dtype=np.bool_),
        )

    def relative_covariance(self, energies_kev: Sequence[float] | Float) -> Float:
        """Covariance of ln eps between the given energies (for correlated activities)."""
        basis = self._basis(np.asarray(energies_kev, dtype=np.float64))
        return np.asarray(basis.T @ self.covariance @ basis, dtype=np.float64)


def fit_efficiency_curve(
    points: Sequence[EfficiencyPoint], order: int = 2, *, geometry: str = ""
) -> EfficiencyCurve:
    """Fit ln eps as a polynomial in ln E by generalised least squares."""
    if not 1 <= order <= MAX_ORDER:
        raise ProcessingError(f"curve order must be between 1 and {MAX_ORDER}")
    pts = tuple(sorted(points, key=lambda p: p.energy_kev))
    n = len(pts)
    if n < order + 1:
        raise ProcessingError(
            f"an efficiency curve of order {order} needs at least {order + 1} points, got {n}"
        )
    kinds = {p.absolute for p in pts}
    if len(kinds) != 1:
        raise ProcessingError("absolute and relative efficiency points cannot be mixed")
    energies = np.array([p.energy_kev for p in pts])
    if np.any(np.diff(energies) <= 0.0):
        raise ProcessingError("two efficiency points have the same energy")
    eff = np.array([p.efficiency for p in pts])
    if np.any(eff <= 0.0):
        raise ProcessingError("efficiencies must be positive")
    if not next(iter(kinds)) and len({p.source for p in pts}) > 1:
        raise ProcessingError(
            "relative efficiency points of several separately normalised groups cannot be "
            "fitted together: their scales are unrelated"
        )
    v = point_covariance(pts)
    if np.any(np.diag(v) <= 0.0):
        raise ProcessingError("every efficiency point needs an uncertainty")
    if np.linalg.cond(v) > 1e12:
        raise ProcessingError(
            "the covariance of the efficiency points is singular: every point needs an "
            "uncertainty of its own (statistical or emission probability)"
        )
    e0 = float(np.exp(np.mean(np.log(energies))))
    log = np.log(energies / e0)
    x = np.vstack([log**k for k in range(order + 1)]).T
    y = np.log(eff)
    v_inv = np.linalg.inv(v)
    normal = x.T @ v_inv @ x
    if np.linalg.cond(normal) > 1e13:
        raise ProcessingError(
            "the points do not determine a curve of this order; use a lower order or a "
            "wider energy range"
        )
    cov = np.linalg.inv(normal)
    a = cov @ (x.T @ v_inv @ y)
    residuals = y - x @ a
    chi2 = float(residuals @ v_inv @ residuals)
    ndf = n - (order + 1)
    p_value = float(chi2_sf(chi2, ndf)) if ndf > 0 else math.nan
    absolute = kinds.pop()
    warnings = list(UNCORRECTED)
    if not absolute:
        warnings.append("relative efficiency: arbitrary units, only ratios are meaningful")
    if not geometry.strip() and absolute:
        warnings.append("no geometry description recorded for this calibration")
    if ndf == 0:
        warnings.append("no redundancy: the curve passes through the points unchecked")
    elif p_value < POOR_FIT_P:
        warnings.append(
            f"points inconsistent with the curve (chi-square {chi2:.1f} for {ndf} degrees of "
            "freedom): check the nuclear data, the peak areas, summing effects and the order"
        )
    return EfficiencyCurve(
        order=order,
        coefficients=np.asarray(a, dtype=np.float64),
        covariance=np.asarray(cov, dtype=np.float64),
        reference_energy_kev=e0,
        points=pts,
        residuals=np.asarray(residuals, dtype=np.float64),
        chi2=chi2,
        ndf=ndf,
        p_value=p_value,
        absolute=absolute,
        geometry=geometry.strip(),
        warnings=tuple(warnings),
    )
