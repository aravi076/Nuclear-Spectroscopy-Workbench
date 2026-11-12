"""Decision threshold, detection limit and coverage interval of a non-negative measurand.

Two selectable methods (``LimitMethod``), chosen per analysis and recorded with it:

``ISO_11929``
    The Bayesian framework of the ISO 11929 series (Weise et al., Radiat. Prot. Dosim.
    121 (2006) 52). With ũ(ỹ) the standard uncertainty of the measurand as a function of
    its true value ỹ:

        decision threshold  y* = k(1-α) ũ(0)
        detection limit     y# = y* + k(1-β) ũ(y#)        (fixed-point iteration)

    ũ includes the uncertainty of the conversion factor w (efficiency, emission
    probability, time, ...): ũ²(ỹ) = w² ũ_N²(ỹ/w) + ỹ² u_rel²(w). The detection limit
    exists only if k(1-β) u_rel(w) < 1. Best estimate and probabilistically symmetric
    coverage interval use the prior ỹ >= 0 (``coverage_interval``).
    Implemented from published literature; conformity with ISO 11929 is **not
    verified** (docs/design/ISO_11929_DETECTION_LIMIT_DESIGN.md).

``CURRIE``
    The classical limits of L. A. Currie, Anal. Chem. 40 (1968) 586, in counts:

        critical level      L_C = k(1-α) σ0,     σ0² = B + u²(B)
        detection limit     L_D = L_C + k(1-β) σ_D,  σ_D² = σ0² + L_D

    i.e. L_D = k² + 2 L_C for α = β; with u²(B) = B (paired blank) this is the familiar
    2.71 + 4.65 √B. Converted to other units by multiplying with w; the uncertainty of
    w is not part of Currie's limits (stated in the result).

For a pure counting model the two methods give the same numbers when u(w) = 0; they
differ when the conversion factor is uncertain and in the coverage interval, which only
the ISO framework defines.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray
from scipy.special import ndtr, ndtri

from nsw.analysis.distributions import normal_sf
from nsw.core.errors import ProcessingError

_MAX_ITERATIONS = 200
_REL_TOLERANCE = 1e-12


class LimitMethod(StrEnum):
    ISO_11929 = "iso11929"
    CURRIE = "currie"


@dataclass(frozen=True, slots=True)
class LimitSettings:
    method: LimitMethod = LimitMethod.ISO_11929
    alpha: float = 0.05  # probability of a false positive (error of the first kind)
    beta: float = 0.05  # probability of a false negative at the detection limit
    gamma: float = 0.05  # 1 - coverage probability of the coverage interval

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "method", LimitMethod(self.method))
        except ValueError as exc:
            raise ProcessingError(f"limit settings: {exc}") from exc
        for name in ("alpha", "beta", "gamma"):
            value = getattr(self, name)
            if not 0.0 < value < 0.5:
                raise ProcessingError(f"limit settings: {name} must be in (0, 0.5), got {value}")

    @property
    def k_alpha(self) -> float:
        return float(ndtri(1.0 - self.alpha))

    @property
    def k_beta(self) -> float:
        return float(ndtri(1.0 - self.beta))


@dataclass(frozen=True, slots=True)
class CharacteristicLimits:
    method: LimitMethod
    decision_threshold: float  # in the unit of the measurand (counts × w)
    detection_limit: float | None  # None: does not exist (see ``note``)
    settings: LimitSettings
    note: str


@dataclass(frozen=True, slots=True)
class CountingModel:
    """Net counts N = gross − background estimate, scaled to the measurand by ``w``.

    ``background`` is the expected background (continuum) count in the peak region and
    ``background_variance`` the variance of its estimate (B itself for a paired blank of
    equal duration; smaller when the continuum is estimated from many channels).
    """

    background: float
    background_variance: float
    w: float = 1.0
    w_rel_uncertainty: float = 0.0

    def __post_init__(self) -> None:
        if not (self.background >= 0.0 and self.background_variance >= 0.0):
            raise ProcessingError("background and its variance must be >= 0")
        if not (self.w > 0.0 and self.w_rel_uncertainty >= 0.0):
            raise ProcessingError("conversion factor w must be > 0 and u_rel(w) >= 0")

    @property
    def sigma0_counts(self) -> float:
        """Standard deviation of the net count when the true net count is zero."""
        return math.sqrt(self.background + self.background_variance)

    def u_tilde(self, true_value: float) -> float:
        """ũ(ỹ) in measurand units: Poisson variance of the signal counts added."""
        n = true_value / self.w
        var_counts = max(n, 0.0) + self.background + self.background_variance
        return math.sqrt(self.w**2 * var_counts + (true_value * self.w_rel_uncertainty) ** 2)


def iso_limits(
    u_tilde: Callable[[float], float], settings: LimitSettings, w_rel_uncertainty: float = 0.0
) -> CharacteristicLimits:
    """ISO 11929 decision threshold and detection limit for a given ũ(ỹ)."""
    y_star = settings.k_alpha * u_tilde(0.0)
    if settings.k_beta * w_rel_uncertainty >= 1.0:
        return CharacteristicLimits(
            LimitMethod.ISO_11929,
            y_star,
            None,
            settings,
            f"detection limit does not exist: k(1-β)·u_rel(w) = "
            f"{settings.k_beta * w_rel_uncertainty:.3g} >= 1",
        )
    # y = 0 is a spurious fixed point when ũ(0) = 0 (no background): start above it.
    # ũ(1)² is the variance added by one unit of the measurand, so k²·ũ(1)² is the
    # exact limit in that case and a safe start otherwise.
    y = 2.0 * y_star if y_star > 0.0 else settings.k_beta**2 * u_tilde(1.0) ** 2
    for _ in range(_MAX_ITERATIONS):
        new = y_star + settings.k_beta * u_tilde(y)
        if abs(new - y) <= _REL_TOLERANCE * max(abs(new), 1e-300):
            y = new
            break
        y = new
    else:
        raise ProcessingError("detection limit iteration did not converge")
    return CharacteristicLimits(
        LimitMethod.ISO_11929,
        y_star,
        y,
        settings,
        "ISO 11929 framework (Bayesian); conformity with the standard not verified",
    )


def currie_limits(model: CountingModel, settings: LimitSettings) -> CharacteristicLimits:
    """Currie critical level and detection limit, converted with ``model.w``."""
    k_a, k_b = settings.k_alpha, settings.k_beta
    s0 = model.sigma0_counts
    l_c = k_a * s0
    # L_D = L_C + k_b sqrt(s0² + L_D)  ->  quadratic in L_D
    b = 2.0 * l_c + k_b**2
    c = l_c**2 - k_b**2 * s0**2
    l_d = 0.5 * (b + math.sqrt(b * b - 4.0 * c))
    note = "Currie (1968) limits in counts, multiplied by w"
    if model.w_rel_uncertainty > 0.0:
        note += f"; the uncertainty of w ({model.w_rel_uncertainty:.3g} relative) is not included"
    return CharacteristicLimits(LimitMethod.CURRIE, l_c * model.w, l_d * model.w, settings, note)


def characteristic_limits(model: CountingModel, settings: LimitSettings) -> CharacteristicLimits:
    """Limits of a counting measurement with the method chosen in ``settings``."""
    if settings.method is LimitMethod.CURRIE:
        return currie_limits(model, settings)
    return iso_limits(model.u_tilde, settings, model.w_rel_uncertainty)


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    best_estimate: float
    lower: float
    upper: float
    coverage: float


def coverage_interval(y: float, u: float, gamma: float = 0.05) -> CoverageInterval:
    """Best estimate and probabilistically symmetric coverage interval for ỹ >= 0.

    With ω = Φ(y/u): ŷ = y + u·φ(y/u)/ω; lower = y − k_p u with p = ω(1 − γ/2);
    upper = y + k_q u with q = 1 − ωγ/2 (k_x the standard normal quantile).
    """
    if not u > 0.0:
        raise ProcessingError("coverage interval needs a standard uncertainty > 0")
    if not 0.0 < gamma < 1.0:
        raise ProcessingError("gamma must be in (0, 1)")
    omega = 1.0 - normal_sf(y / u)
    if omega <= 0.0:
        raise ProcessingError("measured value is too far below zero for a coverage interval")
    best = y + u * math.exp(-0.5 * (y / u) ** 2) / (omega * math.sqrt(2.0 * math.pi))
    lower = y - float(ndtri(omega * (1.0 - gamma / 2.0))) * u
    upper = y + float(ndtri(1.0 - omega * gamma / 2.0)) * u
    return CoverageInterval(best, max(lower, 0.0), upper, 1.0 - gamma)


# ---------------------------------------------------------------------- fit-based ũ(ỹ)
def gaussian_bins(
    channels: NDArray[np.float64], centroid: float, sigma: float
) -> NDArray[np.float64]:
    """Fraction of a unit-area Gaussian falling in each 1-channel bin (as the fit model)."""
    up = (channels + 0.5 - centroid) / sigma
    um = (channels - 0.5 - centroid) / sigma
    out: NDArray[np.float64] = ndtr(up) - ndtr(um)
    return out


def poisson_u_tilde(
    continuum: NDArray[np.float64],
    shape: NDArray[np.float64],
    nuisance: NDArray[np.float64] | None = None,
) -> Callable[[float], float]:
    """ũ(Ñ) of a peak area from the expected Fisher information of a Poisson fit.

    Model m(x) = continuum(x) + Ñ·shape(x) (+ nuisance parameters whose columns are the
    derivatives of m with respect to them, e.g. the continuum parameters). Position and
    width are fixed (at Ñ = 0 they are not defined). ũ²(Ñ) is the area element of the
    inverse information matrix J^T W J with W = 1/m — the same estimator of the
    uncertainty the fit reports, evaluated at a hypothetical true area.
    """
    c = np.asarray(continuum, dtype=np.float64)
    g = np.asarray(shape, dtype=np.float64)
    columns = [g] if nuisance is None else [g, *np.atleast_2d(nuisance)]
    jac = np.column_stack(columns)

    def u(true_area: float) -> float:
        m = np.maximum(c + max(true_area, 0.0) * g, 1e-12)
        info = jac.T @ (jac / m[:, None])
        try:
            cov = np.linalg.inv(info)
        except np.linalg.LinAlgError as exc:  # pragma: no cover - singular design
            raise ProcessingError("the fit region does not determine the peak area") from exc
        return float(math.sqrt(max(cov[0, 0], 0.0)))

    return u


def limits_from_u_tilde(
    u_tilde: Callable[[float], float],
    settings: LimitSettings,
    w: float = 1.0,
    w_rel_uncertainty: float = 0.0,
) -> CharacteristicLimits:
    """Limits of a fitted net area (in counts × w) with the method chosen in ``settings``.

    ISO 11929: from ũ directly. Currie: σ0 = ũ(0) and the classical Poisson growth of the
    variance with the signal, as in ``currie_limits``.
    """
    if settings.method is LimitMethod.CURRIE:
        sigma0 = u_tilde(0.0)
        return currie_limits(CountingModel(0.0, sigma0**2, w, w_rel_uncertainty), settings)

    def scaled(true_value: float) -> float:
        n = true_value / w
        return math.sqrt((w * u_tilde(n)) ** 2 + (true_value * w_rel_uncertainty) ** 2)

    return iso_limits(scaled, settings, w_rel_uncertainty)
