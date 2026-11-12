"""Detector resolution: FWHM as a function of energy (or of channel).

Model
-----
    FWHM(x)^2 = a + b x + c x^2,      a, b, c >= 0,

the usual empirical form: a from electronic noise, b x from the statistics of charge
(HPGe) or light (NaI(Tl)) production, c x^2 from incomplete charge collection or
non-uniformity. x is the energy in keV with FWHM in keV when the peak positions come
from a verified energy calibration; otherwise x is the channel number with FWHM in
channels. For a linear energy scale the form is the same in both variables, so
channel-space analysis of uncalibrated data is legitimate (and labelled as such).

The term c is fitted only when asked for: with few points it is not determined, and
for HPGe detectors it is usually not needed.

Estimation
----------
Weighted least squares on the fitted widths W_i +- u_i themselves,
chi2 = sum (W_i - FWHM(x_i))^2 / u_i^2 (Gauss-Newton), not on W^2: squaring noisy
widths biases them upwards by u^2. The non-negativity constraints are handled exactly
by fitting every subset of the requested terms and keeping the admissible solution of
lowest chi2 (at most seven small fits). A term that ends at zero is reported as
"not supported by the data (fixed at 0)", carries no uncertainty, and is not counted as
a fitted parameter. Covariance: (J^T W J)^-1 of the retained terms.

Derived quantities: relative resolution FWHM(E) / E; FWHM in keV = gain x FWHM in
channels (:func:`fwhm_in_kev`), with both uncertainties propagated.

The uncertainty of a predicted width is statistical only. Predictions outside the range
of the points are flagged as extrapolated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import combinations

import numpy as np
from numpy.typing import NDArray

from nsw.analysis.distributions import chi2_sf
from nsw.core.calibration import (
    CalibrationOrigin,
    CalibrationPoint,
    CalibrationStatus,
    ResolutionCalibration,
)
from nsw.core.errors import ProcessingError

POOR_FIT_P = 1e-3
MODEL_NAME = "sqrt_quadratic"  # FWHM = sqrt(a + b x + c x^2)
TERM_NAMES = ("a", "b", "c")
Float = NDArray[np.float64]


class ResolutionVariable(StrEnum):
    ENERGY_KEV = "energy_keV"  # x in keV, FWHM in keV
    CHANNEL = "channel"  # x in channels, FWHM in channels


@dataclass(frozen=True, slots=True)
class WidthPoint:
    x: float  # energy (keV) or channel
    fwhm: float
    fwhm_unc: float
    label: str = ""


@dataclass(frozen=True)
class ResolutionFit:
    variable: ResolutionVariable
    coefficients: Float  # (a, b, c); terms not fitted or not supported are 0
    covariance: Float  # 3 x 3; zero rows/columns for terms at 0
    fitted_terms: tuple[bool, bool, bool]  # terms with a free, positive estimate
    requested_terms: tuple[bool, bool, bool]
    points: tuple[WidthPoint, ...]
    residuals: Float  # W_i - FWHM(x_i)
    chi2: float
    ndf: int
    p_value: float  # NaN when ndf = 0
    warnings: tuple[str, ...]

    @property
    def unit(self) -> str:
        return "keV" if self.variable is ResolutionVariable.ENERGY_KEV else "channels"

    @property
    def x_range(self) -> tuple[float, float]:
        xs = [p.x for p in self.points]
        return min(xs), max(xs)

    @property
    def normalised_residuals(self) -> Float:
        u = np.array([p.fwhm_unc for p in self.points])
        return np.asarray(self.residuals / u, dtype=np.float64)

    @property
    def poor_fit(self) -> bool:
        return self.ndf > 0 and self.p_value < POOR_FIT_P

    @property
    def verifiable(self) -> bool:
        return self.ndf >= 1 and not self.poor_fit

    @property
    def coefficient_uncertainties(self) -> Float:
        return np.asarray(np.sqrt(np.diag(self.covariance)), dtype=np.float64)

    def fwhm(self, x: float | Sequence[float] | Float) -> tuple[Float, Float, NDArray[np.bool_]]:
        """Predicted FWHM, its standard uncertainty and an extrapolation flag."""
        xv = np.atleast_1d(np.asarray(x, dtype=np.float64))
        a, b, c = self.coefficients
        w2 = a + b * xv + c * xv * xv
        if np.any(w2 <= 0.0):
            raise ProcessingError("the resolution model is not positive at the requested x")
        w = np.sqrt(w2)
        grad = np.vstack([np.ones_like(xv), xv, xv * xv]) / (2.0 * w)  # d FWHM / d(a, b, c)
        var = np.einsum("ik,ij,jk->k", grad, self.covariance, grad)
        lo, hi = self.x_range
        return (
            np.asarray(w, dtype=np.float64),
            np.asarray(np.sqrt(np.maximum(var, 0.0)), dtype=np.float64),
            np.asarray((xv < lo) | (xv > hi), dtype=np.bool_),
        )

    def relative(self, x: float | Sequence[float] | Float) -> tuple[Float, Float]:
        """FWHM / E and its uncertainty. Only defined for the energy variable."""
        if self.variable is not ResolutionVariable.ENERGY_KEV:
            raise ProcessingError(
                "relative resolution FWHM/E needs energies; this model is in channels"
            )
        xv = np.atleast_1d(np.asarray(x, dtype=np.float64))
        if np.any(xv <= 0.0):
            raise ProcessingError("relative resolution needs positive energies")
        w, u, _ = self.fwhm(xv)
        return np.asarray(w / xv, dtype=np.float64), np.asarray(u / xv, dtype=np.float64)

    def to_record(
        self,
        *,
        verified: bool,
        detector_id: str | None = None,
        created: datetime | None = None,
        note: str = "",
    ) -> ResolutionCalibration:
        if verified and not self.verifiable:
            raise ProcessingError(
                "resolution model cannot be verified: it needs at least one redundant point "
                "and a statistically acceptable fit"
            )
        if verified and self.variable is not ResolutionVariable.ENERGY_KEV:
            raise ProcessingError(
                "a resolution model in channel units cannot be verified: it is tied to an "
                "unverified energy scale"
            )
        source = "fitted peak width (nsw.analysis.fitting)"
        return ResolutionCalibration(
            coefficients=tuple(float(v) for v in self.coefficients),
            covariance=tuple(tuple(float(v) for v in row) for row in self.covariance),
            origin=CalibrationOrigin.FITTED,
            status=CalibrationStatus.VERIFIED if verified else CalibrationStatus.UNVERIFIED,
            detector_id=detector_id,
            method=(
                f"weighted least squares on FWHM, terms "
                f"{'+'.join(n for n, f in zip(TERM_NAMES, self.fitted_terms, strict=True) if f)}; "
                f"chi2 = {self.chi2:.3f}, ndf = {self.ndf}; abscissa: {self.variable.value}"
            ),
            created=created,
            # CalibrationPoint.channel holds the abscissa (keV or channel, see ``variable``)
            points=tuple(
                CalibrationPoint(
                    channel=p.x,
                    channel_uncertainty=0.0,
                    reference_value=p.fwhm,
                    reference_uncertainty=p.fwhm_unc,
                    reference_source=source,
                    label=p.label,
                )
                for p in self.points
            ),
            note=note,
            model=MODEL_NAME,
            fwhm_unit="keV" if self.variable is ResolutionVariable.ENERGY_KEV else "channel",
        )


def _fit_subset(
    x: Float, w: Float, u: Float, terms: tuple[int, ...]
) -> tuple[Float, Float, float] | None:
    """Unconstrained Gauss-Newton fit of the given terms. None if it fails or leaves the
    admissible region (all coefficients > 0, model positive at every point)."""
    design2 = np.vstack([x**t for t in terms]).T  # derivative of FWHM^2 wrt coefficients
    weights = 1.0 / (u * u)
    # start: weighted linear fit of W^2 (variance (2 W u)^2)
    w2_weights = 1.0 / (2.0 * w * u) ** 2
    normal = design2.T @ (design2 * w2_weights[:, None])
    if np.linalg.cond(normal) > 1e14:
        return None
    theta = np.linalg.solve(normal, design2.T @ (w2_weights * w * w))
    cov = np.zeros((len(terms), len(terms)))
    for _ in range(100):
        model2 = design2 @ theta
        if np.any(model2 <= 0.0):
            return None
        model = np.sqrt(model2)
        jac = design2 / (2.0 * model[:, None])
        normal = jac.T @ (jac * weights[:, None])
        if np.linalg.cond(normal) > 1e14:
            return None
        cov = np.asarray(np.linalg.inv(normal), dtype=np.float64)
        step = cov @ (jac.T @ (weights * (w - model)))
        theta = theta + step
        if np.all(np.abs(step) <= 1e-12 * (np.abs(theta) + 1e-300)):
            break
    model2 = design2 @ theta
    if np.any(theta <= 0.0) or np.any(model2 <= 0.0):
        return None
    chi2 = float(np.sum(weights * (w - np.sqrt(model2)) ** 2))
    return np.asarray(theta, dtype=np.float64), cov, chi2


def fit_resolution(
    points: Sequence[WidthPoint],
    variable: ResolutionVariable,
    *,
    quadratic: bool = False,
) -> ResolutionFit:
    """Fit FWHM^2 = a + b x (+ c x^2 if ``quadratic``) to fitted peak widths."""
    pts = tuple(sorted(points, key=lambda p: p.x))
    requested = (True, True, bool(quadratic))
    n_requested = sum(requested)
    if len(pts) < 2:
        raise ProcessingError(
            "at least two peak widths at different positions are needed for a resolution model"
        )
    x = np.array([p.x for p in pts], dtype=np.float64)
    w = np.array([p.fwhm for p in pts], dtype=np.float64)
    u = np.array([p.fwhm_unc for p in pts], dtype=np.float64)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(w)) and np.all(np.isfinite(u))):
        raise ProcessingError("width points must be finite numbers")
    if np.any(x <= 0.0) or np.any(w <= 0.0):
        raise ProcessingError("positions and widths must be positive")
    if np.any(u <= 0.0):
        raise ProcessingError("every width needs a positive uncertainty")
    if np.any(np.diff(x) <= 0.0):
        raise ProcessingError("two width points have the same position")
    if len(pts) < n_requested:
        raise ProcessingError(
            f"{n_requested} model terms need at least {n_requested} width points, got {len(pts)}"
        )

    best: tuple[tuple[int, ...], Float, Float, float] | None = None
    allowed = [t for t, on in enumerate(requested) if on]
    for size in range(len(allowed), 0, -1):
        for terms in combinations(allowed, size):
            result = _fit_subset(x, w, u, terms)
            if result is not None and (best is None or result[2] < best[3] - 1e-12):
                best = (terms, *result)
    if best is None:
        raise ProcessingError(
            "no admissible resolution model (all coefficients >= 0) fits these widths"
        )
    terms, theta, cov, chi2 = best
    coefficients = np.zeros(3)
    covariance = np.zeros((3, 3))
    for i, t in enumerate(terms):
        coefficients[t] = theta[i]
        for j, s in enumerate(terms):
            covariance[t, s] = cov[i, j]
    fitted = (0 in terms, 1 in terms, 2 in terms)
    ndf = len(pts) - len(terms)
    p_value = float(chi2_sf(chi2, ndf)) if ndf > 0 else math.nan
    warnings: list[str] = []
    dropped = [TERM_NAMES[t] for t in allowed if t not in terms]
    if dropped:
        warnings.append(
            f"term(s) {', '.join(dropped)} not supported by the data: the unconstrained "
            "estimate is not positive, so the term is fixed at 0 and has no uncertainty"
        )
    if ndf == 0:
        warnings.append("no redundancy: the model passes through the points and cannot be checked")
    elif p_value < POOR_FIT_P:
        warnings.append(
            f"widths inconsistent with the model (chi-square {chi2:.1f} for {ndf} degrees of "
            "freedom): a peak may be a multiplet or Doppler-broadened, or the model is "
            "inadequate"
        )
    model = np.sqrt(coefficients[0] + coefficients[1] * x + coefficients[2] * x * x)
    return ResolutionFit(
        variable=variable,
        coefficients=coefficients,
        covariance=covariance,
        fitted_terms=fitted,
        requested_terms=requested,
        points=pts,
        residuals=np.asarray(w - model, dtype=np.float64),
        chi2=chi2,
        ndf=ndf,
        p_value=p_value,
        warnings=tuple(warnings),
    )


def fwhm_in_kev(
    fwhm_channels: float, fwhm_unc_channels: float, gain: float, gain_unc: float = 0.0
) -> tuple[float, float]:
    """FWHM in keV = gain x FWHM in channels, uncertainties combined in quadrature
    (the two are independent to first order)."""
    if gain <= 0.0 or fwhm_channels <= 0.0:
        raise ProcessingError("gain and FWHM must be positive")
    value = gain * fwhm_channels
    return value, math.hypot(gain * fwhm_unc_channels, fwhm_channels * gain_unc)
