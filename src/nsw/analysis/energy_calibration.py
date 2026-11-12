"""Energy calibration: weighted polynomial regression of reference energy on peak position.

Model
-----
E(ch) = sum_{j=0}^{order} c_j ch^j, channels 1-based. Each reference point i has a fitted
peak position ch_i +- u(ch_i) and a reference energy E_i +- u(E_i). Both uncertainties
are used through the *effective variance*

    v_i = u(E_i)^2 + [E'(ch_i)]^2 u(ch_i)^2,

iterated until the coefficients are stable (the slope E' depends on the fit). The
coefficients minimise chi2 = sum (E_i - E(ch_i))^2 / v_i; their covariance is
(A^T W A)^-1. The regression is done in the scaled variable t = (ch - mean) / half-span
and transformed exactly to powers of ch, which keeps the normal equations well
conditioned for channel numbers of several thousand.

Rules built in
--------------
* One point cannot give gain and offset: at least ``order + 1`` distinct points are
  required to fit at all, and such a fit has no redundancy (chi2 has zero degrees of
  freedom), so it can never be marked verified. Verification needs ``order + 2`` points.
* Every reference energy must carry the source it was taken from.
* The calibration is supported by data only between the lowest and highest reference
  channel; predictions outside are flagged as extrapolated.
* Uncertainties are not silently rescaled. If chi2 is too large the fit is reported as
  poor. The analyst may ask for the covariance to be multiplied by chi2/ndf (Birge
  ratio squared); that choice is recorded in the result and in the calibration record.
* A higher polynomial order has to be justified: :func:`compare_orders` reports whether
  the highest coefficient is significant.

The uncertainty of a calibrated energy at channel ch (:meth:`CalibrationFit.energy`)
is g^T C g with g = (1, ch, ch^2, ...), plus [E'(ch) u(ch)]^2 when the channel itself
has an uncertainty. It is a statistical statement about the fitted curve and does not
cover model error outside the calibrated range.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from nsw.analysis.distributions import chi2_sf
from nsw.core.calibration import (
    CalibrationOrigin,
    CalibrationPoint,
    CalibrationStatus,
    EnergyCalibration,
)
from nsw.core.errors import ProcessingError

POOR_FIT_P = 1e-3
MAX_ORDER = 3
SIGNIFICANT = 3.0  # |coefficient| / uncertainty required to justify a higher order
Float = NDArray[np.float64]


@dataclass(frozen=True)
class CalibrationFit:
    """Result of an energy-calibration regression."""

    order: int
    coefficients: Float  # c_j, powers of channel
    covariance: Float
    points: tuple[CalibrationPoint, ...]
    residuals_kev: Float  # E_i - E(ch_i)
    effective_uncertainties_kev: Float  # sqrt(v_i)
    chi2: float
    ndf: int
    p_value: float  # NaN when ndf = 0
    inflated: bool  # covariance multiplied by chi2/ndf at the analyst's request
    iterations: int
    warnings: tuple[str, ...]
    # scaled-basis representation, used for numerically stable predictions
    _centre: float
    _scale: float
    _scaled_coefficients: Float
    _scaled_covariance: Float

    # ------------------------------------------------------------------ diagnostics
    @property
    def n_points(self) -> int:
        return len(self.points)

    @property
    def channel_range(self) -> tuple[float, float]:
        channels = [p.channel for p in self.points]
        return min(channels), max(channels)

    @property
    def energy_range_kev(self) -> tuple[float, float]:
        lo, hi = self.channel_range
        values, _, _ = self.energy([lo, hi])
        return float(values[0]), float(values[1])

    @property
    def reduced_chi2(self) -> float:
        return self.chi2 / self.ndf if self.ndf > 0 else math.nan

    @property
    def birge_ratio(self) -> float:
        return math.sqrt(self.reduced_chi2) if self.ndf > 0 else math.nan

    @property
    def coefficient_uncertainties(self) -> Float:
        return np.asarray(np.sqrt(np.diag(self.covariance)), dtype=np.float64)

    @property
    def normalised_residuals(self) -> Float:
        return np.asarray(self.residuals_kev / self.effective_uncertainties_kev, dtype=np.float64)

    @property
    def poor_fit(self) -> bool:
        return self.ndf > 0 and self.p_value < POOR_FIT_P

    @property
    def has_redundancy(self) -> bool:
        return self.ndf >= 1

    @property
    def verifiable(self) -> bool:
        """Redundant and statistically acceptable (or explicitly inflated)."""
        return self.has_redundancy and (not self.poor_fit or self.inflated)

    def why_not_verifiable(self) -> str:
        if not self.has_redundancy:
            return (
                f"{self.n_points} points determine the {self.order + 1} coefficients exactly: "
                "there is no redundant point to check the calibration against"
            )
        if self.poor_fit and not self.inflated:
            return (
                f"the points are not consistent with the model (chi-square {self.chi2:.1f} for "
                f"{self.ndf} degrees of freedom): check the line assignments and the model order"
            )
        return ""

    # ------------------------------------------------------------------ prediction
    def _basis(self, channel: Float) -> Float:
        t = (channel - self._centre) / self._scale
        return np.vstack([t**j for j in range(self.order + 1)])

    def energy(
        self, channel: float | Sequence[float] | Float, channel_uncertainty: float | Float = 0.0
    ) -> tuple[Float, Float, NDArray[np.bool_]]:
        """Energy (keV), its standard uncertainty, and an extrapolation flag.

        The uncertainty combines the coefficient covariance with the gain times the
        channel uncertainty, if one is given.
        """
        ch = np.atleast_1d(np.asarray(channel, dtype=np.float64))
        basis = self._basis(ch)
        value = self._scaled_coefficients @ basis
        var = np.einsum("ik,ij,jk->k", basis, self._scaled_covariance, basis)
        gain = self.gain(ch)
        var = var + (gain * np.asarray(channel_uncertainty, dtype=np.float64)) ** 2
        lo, hi = self.channel_range
        return (
            np.asarray(value, dtype=np.float64),
            np.asarray(np.sqrt(np.maximum(var, 0.0)), dtype=np.float64),
            np.asarray((ch < lo) | (ch > hi), dtype=np.bool_),
        )

    def gain(self, channel: float | Sequence[float] | Float) -> Float:
        """dE/dch in keV per channel."""
        ch = np.atleast_1d(np.asarray(channel, dtype=np.float64))
        derivative = [j * c for j, c in enumerate(self.coefficients)][1:]
        return np.asarray(np.polyval(derivative[::-1], ch), dtype=np.float64)

    # ------------------------------------------------------------------ record
    def to_record(
        self,
        *,
        verified: bool,
        detector_id: str | None = None,
        created: datetime | None = None,
        note: str = "",
        synthetic: bool = False,
    ) -> EnergyCalibration:
        """Calibration record for storage and for attaching to spectra.

        ``verified=True`` is refused unless the fit is :attr:`verifiable`.
        """
        if verified and not self.verifiable:
            raise ProcessingError("calibration cannot be verified: " + self.why_not_verifiable())
        method = (
            f"weighted polynomial regression, order {self.order}, effective variance "
            f"(channel and energy uncertainties); chi2 = {self.chi2:.3f}, ndf = {self.ndf}"
        )
        if self.inflated:
            method += f"; covariance multiplied by chi2/ndf = {self.reduced_chi2:.3f}"
        if synthetic:
            note = ("SYNTHETIC reference energies. " + note).strip()
        return EnergyCalibration(
            coefficients=tuple(float(c) for c in self.coefficients),
            covariance=tuple(tuple(float(v) for v in row) for row in self.covariance),
            origin=CalibrationOrigin.FITTED,
            status=CalibrationStatus.VERIFIED if verified else CalibrationStatus.UNVERIFIED,
            detector_id=detector_id,
            method=method,
            created=created,
            points=self.points,
            note=note,
        )


def _transform(order: int, centre: float, scale: float) -> Float:
    """T with c = T b for E = sum_j b_j ((ch - centre)/scale)^j = sum_k c_k ch^k."""
    t = np.zeros((order + 1, order + 1))
    for j in range(order + 1):
        for k in range(j + 1):
            t[k, j] = math.comb(j, k) * (-centre) ** (j - k) / scale**j
    return t


def fit_energy_calibration(
    points: Sequence[CalibrationPoint], order: int = 1, *, inflate: bool = False
) -> CalibrationFit:
    """Fit E(ch) to reference points. Raises :class:`ProcessingError` for invalid input."""
    if not 1 <= order <= MAX_ORDER:
        raise ProcessingError(f"polynomial order must be between 1 and {MAX_ORDER}")
    pts = tuple(sorted(points, key=lambda p: p.channel))
    n = len(pts)
    if n == 1:
        raise ProcessingError(
            "one reference peak cannot determine both gain and offset; at least two "
            "independent reference energies are needed for a straight line"
        )
    if n < order + 1:
        raise ProcessingError(
            f"a polynomial of order {order} needs at least {order + 1} reference points, got {n}"
        )
    x = np.array([p.channel for p in pts], dtype=np.float64)
    y = np.array([p.reference_value for p in pts], dtype=np.float64)
    ux = np.array([p.channel_uncertainty for p in pts], dtype=np.float64)
    uy = np.array([p.reference_uncertainty for p in pts], dtype=np.float64)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        raise ProcessingError("reference points must be finite numbers")
    if np.any(np.diff(x) <= 0.0):
        raise ProcessingError("two reference points have the same channel position")
    if np.any(np.diff(y) <= 0.0):
        raise ProcessingError(
            "reference energies must increase with channel (duplicate energy or wrong assignment)"
        )
    if np.any((ux <= 0.0) & (uy <= 0.0)):
        raise ProcessingError(
            "every reference point needs an uncertainty (of the peak position, of the "
            "reference energy, or both); weights are undefined otherwise"
        )

    centre = float(x.mean())
    scale = float(np.max(np.abs(x - centre)))
    t = (x - centre) / scale
    design = np.vstack([t**j for j in range(order + 1)]).T
    transform = _transform(order, centre, scale)

    slope = np.full(n, (y[-1] - y[0]) / (x[-1] - x[0]))  # starting value of E'(ch)
    b = np.zeros(order + 1)
    cov_b = np.zeros((order + 1, order + 1))
    variance = np.ones(n)
    iterations = 0
    for iterations in range(1, 51):  # noqa: B007
        variance = uy**2 + (slope * ux) ** 2
        weights = 1.0 / variance
        normal = design.T @ (design * weights[:, None])
        if np.linalg.cond(normal) > 1e13:
            raise ProcessingError(
                "the reference points do not determine this polynomial (ill-conditioned "
                "normal equations); use a lower order or better separated points"
            )
        cov_b = np.asarray(np.linalg.inv(normal), dtype=np.float64)
        new_b = cov_b @ (design.T @ (weights * y))
        c = transform @ new_b
        derivative = [j * cj for j, cj in enumerate(c)][1:]
        slope = np.polyval(derivative[::-1], x)
        converged = np.allclose(new_b, b, rtol=1e-13, atol=1e-13 * float(np.max(np.abs(y))))
        b = new_b
        if converged:
            break
    residuals = y - design @ b
    chi2 = float(np.sum(residuals**2 / variance))
    ndf = n - (order + 1)
    p_value = float(chi2_sf(chi2, ndf)) if ndf > 0 else math.nan

    warnings: list[str] = []
    inflated = False
    if inflate:
        if ndf < 1:
            raise ProcessingError("uncertainties cannot be inflated without redundant points")
        if chi2 / ndf > 1.0:
            cov_b = cov_b * (chi2 / ndf)
            inflated = True
            warnings.append(
                f"covariance multiplied by chi2/ndf = {chi2 / ndf:.2f} at the analyst's "
                "request: the stated point uncertainties do not explain the scatter"
            )
    if ndf == 0:
        warnings.append(
            "no redundancy: the curve passes exactly through the points and cannot be "
            "checked; such a calibration cannot be verified"
        )
    elif p_value < POOR_FIT_P and not inflated:
        warnings.append(
            f"points inconsistent with the model (chi-square {chi2:.1f} for {ndf} degrees of "
            "freedom): check the assignments, the uncertainties and the order"
        )
    if np.any(slope <= 0.0):
        raise ProcessingError(
            "the fitted energy scale is not increasing over the calibrated range; the "
            "points or the order are wrong"
        )
    missing_source = [p.label or f"channel {p.channel:g}" for p in pts if not p.reference_source]
    if missing_source:  # cannot happen via CalibrationPoint (min_length=1); guard anyway
        raise ProcessingError("reference energies without a source: " + ", ".join(missing_source))

    return CalibrationFit(
        order=order,
        coefficients=np.asarray(transform @ b, dtype=np.float64),
        covariance=np.asarray(transform @ cov_b @ transform.T, dtype=np.float64),
        points=pts,
        residuals_kev=np.asarray(residuals, dtype=np.float64),
        effective_uncertainties_kev=np.asarray(np.sqrt(variance), dtype=np.float64),
        chi2=chi2,
        ndf=ndf,
        p_value=p_value,
        inflated=inflated,
        iterations=iterations,
        warnings=tuple(warnings),
        _centre=centre,
        _scale=scale,
        _scaled_coefficients=np.asarray(b, dtype=np.float64),
        _scaled_covariance=np.asarray(cov_b, dtype=np.float64),
    )


@dataclass(frozen=True, slots=True)
class OrderComparison:
    lower: CalibrationFit
    higher: CalibrationFit | None  # None if there are too few points for the higher order
    highest_coefficient_significance: float  # |c| / u(c) of the added term; NaN if not fitted
    justified: bool
    statement: str


def compare_orders(points: Sequence[CalibrationPoint], order: int = 1) -> OrderComparison:
    """Is a polynomial of ``order + 1`` justified over ``order``?

    The higher order is called justified only if it can be fitted with at least one
    redundant point and its highest coefficient differs from zero by at least 3
    standard uncertainties. With too few points the question cannot be answered and
    the lower order stands.
    """
    lower = fit_energy_calibration(points, order)
    if order + 1 > MAX_ORDER or len(points) < order + 3:
        return OrderComparison(
            lower,
            None,
            math.nan,
            False,
            f"order {order + 1} needs at least {order + 3} points to be tested "
            f"({len(points)} given); order {order} is kept",
        )
    higher = fit_energy_calibration(points, order + 1)
    significance = abs(float(higher.coefficients[-1])) / float(higher.coefficient_uncertainties[-1])
    justified = significance >= SIGNIFICANT
    if justified:
        statement = (
            f"the order-{order + 1} term is significant ({significance:.1f} standard "
            f"uncertainties; chi-square {lower.chi2:.1f}/{lower.ndf} -> "
            f"{higher.chi2:.1f}/{higher.ndf}); order {order + 1} is justified"
        )
    else:
        statement = (
            f"the order-{order + 1} term is not significant ({significance:.1f} standard "
            f"uncertainties); order {order} is sufficient"
        )
    return OrderComparison(lower, higher, significance, justified, statement)


# ---------------------------------------------------------------------- stability
@dataclass(frozen=True, slots=True)
class StabilityResult:
    """Constancy test of one peak position measured in several independent spectra."""

    n: int
    weighted_mean: float
    mean_uncertainty: float
    chi2: float
    ndf: int
    p_value: float
    max_deviation: float  # largest |position - weighted mean|, channels
    relative_spread: float  # max_deviation / weighted mean
    stable: bool  # p >= 0.001
    statement: str


def position_stability(
    positions: Sequence[float], uncertainties: Sequence[float]
) -> StabilityResult:
    """Are several measurements of the same peak position consistent with one value?

    chi2 = sum (x_i - mean)^2 / u_i^2 with the weighted mean; ndf = n - 1. The
    measurements must come from independent acquisitions (never from successive saves
    of one acquisition).
    """
    x = np.asarray(positions, dtype=np.float64)
    u = np.asarray(uncertainties, dtype=np.float64)
    if x.size < 2 or x.size != u.size:
        raise ProcessingError("stability needs at least two positions with uncertainties")
    if np.any(u <= 0.0) or not np.all(np.isfinite(u)) or not np.all(np.isfinite(x)):
        raise ProcessingError("positions need finite, positive uncertainties")
    w = 1.0 / u**2
    mean = float(np.sum(w * x) / np.sum(w))
    chi2 = float(np.sum(w * (x - mean) ** 2))
    ndf = int(x.size - 1)
    p = float(chi2_sf(chi2, ndf))
    max_dev = float(np.max(np.abs(x - mean)))
    stable = p >= POOR_FIT_P
    statement = (
        f"{x.size} positions agree with a common value {mean:.3f} within their "
        f"uncertainties (chi-square {chi2:.1f} for {ndf}, p = {p:.3g}); largest deviation "
        f"{max_dev:.3f} channels"
        if stable
        else f"the positions differ by more than their uncertainties allow (chi-square "
        f"{chi2:.1f} for {ndf}, p = {p:.3g}); largest deviation {max_dev:.3f} channels "
        f"({max_dev / mean:.2e} relative): the gain was not constant, or a position "
        "uncertainty is underestimated"
    )
    return StabilityResult(
        n=int(x.size),
        weighted_mean=mean,
        mean_uncertainty=float(1.0 / math.sqrt(np.sum(w))),
        chi2=chi2,
        ndf=ndf,
        p_value=p,
        max_deviation=max_dev,
        relative_spread=max_dev / abs(mean) if mean != 0.0 else math.nan,
        stable=stable,
        statement=statement,
    )
