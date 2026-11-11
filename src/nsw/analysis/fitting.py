"""Gaussian peak fitting in channel space, with covariance and goodness-of-fit diagnostics.

Model
-----
A spectrum is a histogram, so the expectation of channel x (1-based, covering
x - 0.5 .. x + 0.5) for a Gaussian line of area N, centroid mu and standard deviation
sigma is the *integral* of the Gaussian over the channel,

    p(x) = N [Phi((x + 0.5 - mu) / sigma) - Phi((x - 0.5 - mu) / sigma)],

not the density at the channel centre (the difference is a width bias of
1/(24 sigma^2) relative, about 1.4 % in sigma for a FWHM of 4 channels). The fitted
parameters are N, mu and sigma. Derived quantities:

    FWHM      = 2 sqrt(2 ln 2) sigma = 2.35482 sigma
    amplitude = N / (sigma sqrt(2 pi))        [G(x) = A exp(-(x - mu)^2 / (2 sigma^2))]

Continuum under the peaks, over the fit region xl..xr:

    none      0
    constant  b
    linear    bL + (bR - bL) (x - xl) / (xr - xl)   (parameters = values at the two ends)

Optional step (only where physically justified, e.g. small-angle scattering into the
low-energy side of an HPGe peak): each peak adds f A_j [1 - Phi((x - mu_j) / sigma_j)]
with one common step fraction f >= 0.

Estimators
----------
``poisson``  Maximum likelihood for independent Poisson counts n_x with expectation
             m_x. Minimises the deviance D = 2 sum [m - n + n ln(n / m)] (the n ln term
             is zero for n = 0), so empty channels contribute 2 m and need no special
             treatment. Areas and continuum are constrained to be >= 0. Only valid for
             count data; refused for net (subtracted) spectra.
``wls``      Weighted least squares, chi2 = sum (y - m)^2 / var, with the variance
             carried by the spectrum. This is the estimator for net spectra, whose
             values are not Poisson distributed and may be negative. It is refused if
             any channel of the region has zero variance. For count data the weights
             1/n bias the result towards low values when counts are small.
``joint``    (:func:`fit_peaks_joint`) Gross spectrum G and an independent background
             measurement B (live-time ratio alpha) are fitted together:
             G_x ~ Poisson(alpha beta_x + s_x), B_x ~ Poisson(beta_x), where s_x is the
             model above (source contribution only) and the unknown background
             expectation beta_x of every channel is a nuisance parameter that is
             eliminated analytically (profile likelihood). No subtraction is formed and
             no channel needs a non-zero count.

All three are minimised with the same damped Fisher-scoring iteration. The covariance
matrix is the inverse of the expected Fisher information J^T W J at the solution
(W = 1/m for Poisson, 1/var for WLS, 1/(mu_G + alpha^2 beta) for the joint fit, which is
the information left after eliminating beta_x). Uncertainties are *not* rescaled by
sqrt(chi2/ndf).

What a converged fit does not show
----------------------------------
* That the model is right: check chi2/ndf, the residuals and the runs test.
* That the components of a multiplet are resolved: see :func:`multiplet_evidence`.
* An energy: positions are channels unless the spectrum has a verified calibration.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations, pairwise

import numpy as np
from numpy.typing import NDArray
from scipy.special import ndtr, xlogy
from scipy.stats import chi2 as chi2_distribution
from scipy.stats import norm as normal_distribution
from scipy.stats import poisson as poisson_distribution

from nsw.analysis.background import check_compatibility
from nsw.analysis.peaks import PeakSearchResult
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum

FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_TINY = 1e-12
_MIN_SIGMA = 0.25  # channels
_MAX_STEP = 10.0
LOW_EXPECTATION = 5.0  # below this the chi-square distribution of the statistics is doubtful
STRONG_EVIDENCE_BIC = 10.0
POOR_FIT_P = 1e-3
JOINT_LOW_BACKGROUND = 5.0  # mean background counts per channel below which to warn
MIN_SIGNIFICANCE = 3.0  # area / uncertainty below which a component is called insignificant

Float = NDArray[np.float64]


class FitMethod(StrEnum):
    AUTO = "auto"  # poisson for count data, wls for derived data
    POISSON = "poisson"
    WLS = "wls"


class BackgroundShape(StrEnum):
    NONE = "none"
    CONSTANT = "constant"
    LINEAR = "linear"


@dataclass(frozen=True, slots=True)
class FitSettings:
    method: FitMethod = FitMethod.AUTO
    background: BackgroundShape = BackgroundShape.LINEAR
    shared_width: bool = True  # one sigma for all peaks of a multiplet
    step: bool = False
    max_iterations: int = 300
    tolerance: float = 1e-7  # on the expected remaining decrease of the statistic
    # Hold parameters at the supplied values (positions: ``centroids``; widths:
    # ``fwhm_channels``). For weak peaks whose position and width are known from
    # calibrations: a free shape lets the fit follow fluctuations and biases the area.
    # The uncertainty of a fixed value is NOT propagated into the other parameters.
    fixed_centroids: bool = False
    fixed_width: bool = False

    def __post_init__(self) -> None:
        # accept the plain string values too (e.g. from a GUI control or a stored record)
        try:
            object.__setattr__(self, "method", FitMethod(self.method))
            object.__setattr__(self, "background", BackgroundShape(self.background))
        except ValueError as exc:
            raise ProcessingError(f"fit settings: {exc}") from exc
        if self.max_iterations < 1 or not self.tolerance > 0.0:
            raise ProcessingError("fit settings: max_iterations >= 1 and tolerance > 0 required")


@dataclass(frozen=True, slots=True)
class FittedPeak:
    """One Gaussian component. Uncertainties are standard deviations from the covariance
    matrix (NaN when the parameter ended on a bound or the covariance is unavailable)."""

    centroid: float  # channel
    centroid_unc: float
    area: float  # counts
    area_unc: float
    sigma: float  # channels
    sigma_unc: float
    fwhm: float  # channels
    fwhm_unc: float
    amplitude: float  # counts per channel at the centroid (density form)
    amplitude_unc: float
    energy_kev: float | None  # only with a verified calibration
    at_bound: tuple[str, ...]  # names of this peak's parameters that ended on a bound

    @property
    def significance(self) -> float:
        """Area divided by its standard uncertainty (NaN if the uncertainty is unknown)."""
        if not math.isfinite(self.area_unc) or self.area_unc <= 0.0:
            return math.nan
        return self.area / self.area_unc


@dataclass(frozen=True)
class FitResult:
    method: str  # "poisson", "wls" or "joint-poisson"
    first_channel: int
    last_channel: int
    parameter_names: tuple[str, ...]
    values: Float
    covariance: Float | None  # NaN rows/columns for parameters on a bound
    peaks: tuple[FittedPeak, ...]
    background: tuple[tuple[str, float, float], ...]  # (name, value, standard uncertainty)
    step_fraction: tuple[float, float] | None
    channels: Float
    data: Float  # fitted data (for the joint fit: gross counts)
    fitted: Float  # model expectation for ``data``
    fitted_continuum: Float  # everything except the Gaussians
    residuals: Float  # normalised residuals
    statistic: float  # deviance (poisson, joint) or chi-square (wls)
    statistic_name: str
    pearson_chi2: float
    ndf: int
    p_value: float  # goodness-of-fit probability (NaN if none can be given)
    gof_method: str  # how p_value was obtained
    runs_z: float | None  # Wald-Wolfowitz runs test on the residual signs
    converged: bool
    iterations: int
    flags: tuple[str, ...]
    warnings: tuple[str, ...]
    spectrum_sha256: str
    is_synthetic: bool
    settings: FitSettings

    @property
    def n_peaks(self) -> int:
        return len(self.peaks)

    @property
    def reduced_chi2(self) -> float:
        return self.pearson_chi2 / self.ndf if self.ndf > 0 else math.nan

    @property
    def ok(self) -> bool:
        """Converged with a usable covariance and no parameter on a bound.

        This says nothing about whether the model describes the data: see ``poor_fit``.
        """
        return self.converged and not self.flags

    @property
    def poor_fit(self) -> bool:
        """True if the goodness-of-fit probability is below ``POOR_FIT_P``."""
        return math.isfinite(self.p_value) and self.p_value < POOR_FIT_P

    def correlation(self, a: str, b: str) -> float:
        if self.covariance is None:
            return math.nan
        i, j = self.parameter_names.index(a), self.parameter_names.index(b)
        c = self.covariance
        return float(c[i, j] / math.sqrt(c[i, i] * c[j, j]))


# ---------------------------------------------------------------------- model
@dataclass(frozen=True, slots=True)
class _Layout:
    n_peaks: int
    shared_width: bool
    step: bool
    background: BackgroundShape

    @property
    def n_sigma(self) -> int:
        return 1 if self.shared_width else self.n_peaks

    @property
    def n_background(self) -> int:
        return {BackgroundShape.NONE: 0, BackgroundShape.CONSTANT: 1, BackgroundShape.LINEAR: 2}[
            self.background
        ]

    @property
    def n_parameters(self) -> int:
        return 2 * self.n_peaks + self.n_sigma + int(self.step) + self.n_background

    def sigma_index(self, peak: int) -> int:
        return 2 * self.n_peaks + (0 if self.shared_width else peak)

    @property
    def step_index(self) -> int:
        return 2 * self.n_peaks + self.n_sigma

    @property
    def background_index(self) -> int:
        return 2 * self.n_peaks + self.n_sigma + int(self.step)

    def names(self) -> tuple[str, ...]:
        k = self.n_peaks
        out = [f"area{j + 1}" for j in range(k)] + [f"centroid{j + 1}" for j in range(k)]
        out += ["sigma"] if self.shared_width else [f"sigma{j + 1}" for j in range(k)]
        if self.step:
            out.append("step")
        if self.background is BackgroundShape.CONSTANT:
            out.append("background")
        elif self.background is BackgroundShape.LINEAR:
            out += ["background_left", "background_right"]
        return tuple(out)


def _phi(u: Float) -> Float:
    return np.asarray(np.exp(-0.5 * u * u) / _SQRT_2PI, dtype=np.float64)


def model_and_jacobian(x: Float, theta: Float, layout: _Layout) -> tuple[Float, Float, Float]:
    """Expectation per channel, its Jacobian (channels x parameters), and the continuum."""
    k = layout.n_peaks
    m = np.zeros(x.size, dtype=np.float64)
    jac = np.zeros((x.size, layout.n_parameters), dtype=np.float64)
    continuum = np.zeros(x.size, dtype=np.float64)
    f = float(theta[layout.step_index]) if layout.step else 0.0
    for j in range(k):
        area, mu = float(theta[j]), float(theta[k + j])
        i_s = layout.sigma_index(j)
        sigma = float(theta[i_s])
        up, um = (x + 0.5 - mu) / sigma, (x - 0.5 - mu) / sigma
        d_phi = ndtr(up) - ndtr(um)
        pu, pm = _phi(up), _phi(um)
        m += area * d_phi
        jac[:, j] += d_phi
        jac[:, k + j] += area * (pm - pu) / sigma
        jac[:, i_s] += area * (um * pm - up * pu) / sigma
        if layout.step:
            v = (x - mu) / sigma
            q = ndtr(-v)
            pv = _phi(v)
            unit = q / (sigma * _SQRT_2PI)
            step = f * area * unit
            m += step
            continuum += step
            jac[:, layout.step_index] += area * unit
            jac[:, j] += f * unit
            jac[:, k + j] += f * area * pv / (sigma * sigma * _SQRT_2PI)
            jac[:, i_s] += f * area * (v * pv - q) / (sigma * sigma * _SQRT_2PI)
    i_b = layout.background_index
    if layout.background is BackgroundShape.CONSTANT:
        m += theta[i_b]
        continuum += theta[i_b]
        jac[:, i_b] = 1.0
    elif layout.background is BackgroundShape.LINEAR:
        t = (x - x[0]) / (x[-1] - x[0])
        line = theta[i_b] + (theta[i_b + 1] - theta[i_b]) * t
        m += line
        continuum += line
        jac[:, i_b] = 1.0 - t
        jac[:, i_b + 1] = t
    return m, jac, continuum


# ---------------------------------------------------------------------- statistics
class _Statistic:
    """Per-channel deviance d, score u = d(log L)/dm and information weight w."""

    name = ""

    def terms(self, m: Float) -> tuple[Float, Float, Float]:
        raise NotImplementedError

    def expectation(self, m: Float) -> Float:
        """Expectation of the fitted data for model value m."""
        return m


class _Poisson(_Statistic):
    name = "Poisson deviance"

    def __init__(self, n: Float) -> None:
        self.n = n

    def terms(self, m: Float) -> tuple[Float, Float, Float]:
        me = np.maximum(m, _TINY)
        d = 2.0 * (me - self.n + xlogy(self.n, self.n) - xlogy(self.n, me))
        return np.asarray(d), self.n / me - 1.0, 1.0 / me


class _WeightedLeastSquares(_Statistic):
    name = "chi-square"

    def __init__(self, y: Float, variance: Float) -> None:
        self.y, self.variance = y, variance

    def terms(self, m: Float) -> tuple[Float, Float, Float]:
        r = self.y - m
        return r * r / self.variance, r / self.variance, 1.0 / self.variance


class _JointPoisson(_Statistic):
    """Gross G ~ Poisson(alpha beta + s), background B ~ Poisson(beta); beta profiled out.

    For a given source expectation s the maximising beta solves
        alpha (1 + alpha) beta^2 + [(1 + alpha) s - alpha (G + B)] beta - B s = 0
    (larger root). The source term s may be negative, as long as alpha beta + s >= 0,
    which the larger root guarantees; the source continuum is therefore not constrained
    to be positive, which would bias the peak area when the true continuum is near zero.
    """

    name = "profile Poisson deviance (gross + background)"

    def __init__(self, gross: Float, background: Float, alpha: float) -> None:
        self.g, self.b, self.alpha = gross, background, alpha

    def beta(self, s: Float) -> Float:
        a = self.alpha * (1.0 + self.alpha)
        b = (1.0 + self.alpha) * s - self.alpha * (self.g + self.b)
        # the discriminant is >= 0 analytically (also for s < 0); clip rounding errors
        root = np.sqrt(np.maximum(b * b + 4.0 * a * self.b * s, 0.0))
        # two algebraically equal forms; each is used where it does not cancel
        with np.errstate(divide="ignore", invalid="ignore"):
            stable = np.where(b > 0.0, 2.0 * self.b * s / (b + root), (root - b) / (2.0 * a))
        return np.asarray(np.where(np.isfinite(stable), stable, 0.0), dtype=np.float64)

    def terms(self, m: Float) -> tuple[Float, Float, Float]:
        s = m  # may be negative: only the gross expectation alpha*beta + s must be >= 0
        beta = self.beta(s)
        mu = np.maximum(self.alpha * beta + s, _TINY)
        be = np.maximum(beta, _TINY)
        d = 2.0 * (
            (mu - self.g + xlogy(self.g, self.g) - xlogy(self.g, mu))
            + (beta - self.b + xlogy(self.b, self.b) - xlogy(self.b, be))
        )
        w = 1.0 / np.maximum(mu + self.alpha**2 * beta, _TINY)
        u = self.g / mu - 1.0
        # Empty gross channel with s below -alpha B / (1 + alpha): the constraint
        # alpha beta + s >= 0 is active (beta = -s / alpha) and the log-likelihood is
        # B ln(-s / alpha) + s / alpha, whose derivative joins the free branch continuously.
        pinned = (self.g == 0.0) & (s < -self.alpha * self.b / (1.0 + self.alpha))
        if np.any(pinned):
            u = np.where(pinned, self.b / np.minimum(s, -_TINY) + 1.0 / self.alpha, u)
        return np.asarray(np.maximum(d, 0.0)), np.asarray(u, dtype=np.float64), w

    def expectation(self, m: Float) -> Float:
        return np.asarray(np.maximum(self.alpha * self.beta(m) + m, 0.0), dtype=np.float64)


# ---------------------------------------------------------------------- minimiser
@dataclass(slots=True)
class _Solution:
    theta: Float
    converged: bool
    iterations: int
    free: NDArray[np.bool_]
    information: Float
    statistic: float


def _minimise(
    x: Float,
    layout: _Layout,
    stat: _Statistic,
    theta0: Float,
    lower: Float,
    upper: Float,
    settings: FitSettings,
) -> _Solution:
    """Damped Fisher scoring with an active set for the bounds."""
    theta = np.clip(theta0, lower, upper)
    m, jac, _ = model_and_jacobian(x, theta, layout)
    d, u, w = stat.terms(m)
    q = float(d.sum())
    lam = 1e-3
    converged = False
    free = np.ones(theta.size, dtype=np.bool_)
    info = (jac * w[:, None]).T @ jac
    iterations = 0
    for iterations in range(1, settings.max_iterations + 1):  # noqa: B007
        g = jac.T @ u
        info = (jac * w[:, None]).T @ jac
        free = ~(((theta <= lower) & (g < 0.0)) | ((theta >= upper) & (g > 0.0)))
        free &= lower < upper  # lower == upper marks a parameter held fixed
        if not free.any():
            converged = True
            break
        scale = np.sqrt(np.diag(info)[free])
        if np.any(scale <= 0.0) or not np.all(np.isfinite(scale)):
            break  # a free parameter has no influence on the model: singular
        fn = info[np.ix_(free, free)] / np.outer(scale, scale)
        gn = g[free] / scale
        newton, *_ = np.linalg.lstsq(fn, gn, rcond=None)
        decrement = float(gn @ newton)
        if decrement < settings.tolerance:
            converged = True
            break
        accepted = False
        while lam < 1e12:
            delta, *_ = np.linalg.lstsq(fn + lam * np.eye(fn.shape[0]), gn, rcond=None)
            trial = theta.copy()
            trial[free] += delta / scale
            trial = np.clip(trial, lower, upper)
            m_t, jac_t, _ = model_and_jacobian(x, trial, layout)
            d_t, u_t, w_t = stat.terms(m_t)
            q_t = float(d_t.sum())
            if math.isfinite(q_t) and q_t < q:
                theta, jac, u, w, q = trial, jac_t, u_t, w_t, q_t
                lam = max(lam / 5.0, 1e-9)
                accepted = True
                break
            lam *= 4.0
        if not accepted:
            # no downhill step exists at machine precision; accept if statistically at the
            # optimum (remaining expected decrease far below one unit of the statistic)
            converged = decrement < 1e-3
            break
    return _Solution(theta, converged, iterations, free, info, q)


def _covariance(sol: _Solution) -> Float | None:
    n = sol.theta.size
    cov = np.full((n, n), np.nan, dtype=np.float64)
    free = sol.free
    if not free.any():
        return None
    sub = sol.information[np.ix_(free, free)]
    scale = np.sqrt(np.diag(sub))
    if np.any(scale <= 0.0) or not np.all(np.isfinite(scale)):
        return None
    normalised = sub / np.outer(scale, scale)
    if np.linalg.cond(normalised) > 1e12:
        return None
    inv = np.linalg.inv(normalised) / np.outer(scale, scale)
    if np.any(np.diag(inv) <= 0.0) or not np.all(np.isfinite(inv)):
        return None
    cov[np.ix_(free, free)] = inv
    return cov


def poisson_deviance_moments(m: Float) -> tuple[Float, Float]:
    """Mean and variance of the unit deviance d = 2 [m - n + n ln(n/m)] for n ~ Poisson(m).

    Exact (summed over the Poisson distribution) for m <= 100; for larger m the limits
    1 + 1/(6m) and 2 are used (error below 1 %). At low m the mean is far from the
    chi-square value 1 (it tends to 2m for m -> 0), which is why chi-square tables cannot
    judge a low-count fit.
    """
    m = np.asarray(m, dtype=np.float64)
    mean = 1.0 + 1.0 / (6.0 * np.maximum(m, 100.0))
    var = np.full(m.shape, 2.0)
    small = m <= 100.0
    if np.any(small):
        ms = np.maximum(m[small], _TINY)[:, None]
        n = np.arange(0.0, 241.0)[None, :]
        pmf = poisson_distribution.pmf(n, ms)
        d = 2.0 * (ms - n + xlogy(n, n) - xlogy(n, ms))
        e1 = (pmf * d).sum(axis=1)
        e2 = (pmf * d * d).sum(axis=1)
        mean[small] = e1
        var[small] = np.maximum(e2 - e1 * e1, 0.0)
    return mean, var


def _runs_z(residuals: Float) -> float | None:
    signs = np.sign(residuals[residuals != 0.0])
    n1, n2 = int((signs > 0).sum()), int((signs < 0).sum())
    if n1 == 0 or n2 == 0:
        return None
    runs = 1 + int(np.count_nonzero(signs[1:] != signs[:-1]))
    total = n1 + n2
    mean = 2.0 * n1 * n2 / total + 1.0
    var = 2.0 * n1 * n2 * (2.0 * n1 * n2 - total) / (total * total * (total - 1.0))
    if var <= 0.0:
        return None
    return (runs - mean) / math.sqrt(var)


# ---------------------------------------------------------------------- set-up helpers
def _region(spectrum: Spectrum, first_channel: int, last_channel: int) -> tuple[int, int]:
    lo = first_channel - spectrum.first_channel
    hi = last_channel - spectrum.first_channel + 1
    if not 0 <= lo < hi <= spectrum.n_channels:
        raise ProcessingError(f"fit region {first_channel}..{last_channel} is outside the spectrum")
    return lo, hi


def _widths(fwhm_channels: float | Sequence[float], k: int) -> list[float]:
    values = (
        [float(fwhm_channels)] * k
        if isinstance(fwhm_channels, int | float)
        else [float(w) for w in fwhm_channels]
    )
    if len(values) != k:
        raise ProcessingError("one FWHM guess per peak (or a single value) is required")
    if any(not math.isfinite(w) or w <= 0.0 for w in values):
        raise ProcessingError("FWHM guesses must be > 0")
    return values


def _start_and_bounds(
    x: Float,
    y: Float,
    centroids: Sequence[float],
    fwhm: list[float],
    layout: _Layout,
    non_negative: bool,
    continuum_non_negative: bool | None = None,
) -> tuple[Float, Float, Float]:
    if continuum_non_negative is None:
        continuum_non_negative = non_negative
    k = layout.n_peaks
    n = layout.n_parameters
    theta = np.zeros(n)
    lower = np.full(n, -np.inf)
    upper = np.full(n, np.inf)
    n_edge = int(min(5, max(1, x.size // 8)))
    b_left, b_right = float(y[:n_edge].mean()), float(y[-n_edge:].mean())
    if layout.background is BackgroundShape.NONE:
        b_left = b_right = 0.0
    elif layout.background is BackgroundShape.CONSTANT:
        b_left = b_right = 0.5 * (b_left + b_right)
    if continuum_non_negative:
        b_left, b_right = max(b_left, 0.01), max(b_right, 0.01)
    t = (x - x[0]) / (x[-1] - x[0])
    baseline = b_left + (b_right - b_left) * t
    sigmas = [w / FWHM_PER_SIGMA for w in fwhm]
    for j, c in enumerate(centroids):
        i = int(np.clip(round(c - x[0]), 0, x.size - 1))
        a, b = max(0, i - 1), min(x.size, i + 2)
        height = float((y[a:b] - baseline[a:b]).mean())
        theta[j] = max(height * sigmas[j] * _SQRT_2PI, 1.0)
        theta[k + j] = c
        lower[k + j], upper[k + j] = x[0], x[-1]
        if non_negative:
            lower[j] = 0.0
    for j in range(layout.n_sigma):
        i_s = 2 * k + j
        theta[i_s] = float(np.mean(sigmas)) if layout.shared_width else sigmas[j]
        lower[i_s], upper[i_s] = _MIN_SIGMA, 0.5 * (x[-1] - x[0])
    if layout.step:
        theta[layout.step_index] = 0.01
        lower[layout.step_index], upper[layout.step_index] = 0.0, _MAX_STEP
    i_b = layout.background_index
    if layout.background is BackgroundShape.CONSTANT:
        theta[i_b] = b_left
    elif layout.background is BackgroundShape.LINEAR:
        theta[i_b], theta[i_b + 1] = b_left, b_right
    if continuum_non_negative:
        lower[i_b : i_b + layout.n_background] = 0.0
    return theta, lower, upper


def _hold_fixed(
    theta: Float, lower: Float, upper: Float, layout: _Layout, settings: FitSettings
) -> None:
    """Mark positions and/or widths as fixed at their starting values (lower == upper)."""
    k = layout.n_peaks
    indices: list[int] = []
    if settings.fixed_centroids:
        indices += list(range(k, 2 * k))
    if settings.fixed_width:
        indices += list(range(2 * k, 2 * k + layout.n_sigma))
    for i in indices:
        lower[i] = upper[i] = theta[i]


def _check_inputs(
    spectrum: Spectrum, centroids: Sequence[float], first_channel: int, last_channel: int
) -> None:
    if spectrum.metadata.get("display_only") == "true":
        raise ProcessingError("a display-only (smoothed) spectrum must not be fitted")
    if not centroids:
        raise ProcessingError("at least one peak position is required")
    for c in centroids:
        if not first_channel <= c <= last_channel:
            raise ProcessingError(
                f"peak position {c:g} is outside the fit region {first_channel}..{last_channel}"
            )


def _build_result(
    *,
    method: str,
    spectrum: Spectrum,
    first_channel: int,
    x: Float,
    data: Float,
    stat: _Statistic,
    layout: _Layout,
    sol: _Solution,
    lower: Float,
    upper: Float,
    residual_variance: str,
    settings: FitSettings,
    extra_warnings: Sequence[str] = (),
    net: Float | None = None,
) -> FitResult:
    theta = sol.theta
    names = layout.names()
    m, _, continuum = model_and_jacobian(x, theta, layout)
    d, _, w = stat.terms(m)
    expected = stat.expectation(m)
    k = layout.n_peaks
    flags: list[str] = []
    warnings: list[str] = list(extra_warnings)
    if not sol.converged:
        flags.append("not converged")
    cov = _covariance(sol)
    if cov is None:
        flags.append("covariance not available (singular information matrix)")
    fixed = lower == upper
    on_bound = [
        names[i]
        for i in range(theta.size)
        if not fixed[i] and (theta[i] <= lower[i] or theta[i] >= upper[i])
    ]
    if np.any(fixed):
        warnings.append(
            "held fixed at the supplied values: "
            + ", ".join(names[i] for i in range(theta.size) if fixed[i])
            + "; their uncertainty is not included in the uncertainties of the other "
            "parameters"
        )
    # a continuum or step that is zero within the constraint is a legitimate estimate
    harmless = {"step", "background", "background_left", "background_right"}
    for name in on_bound:
        if name not in harmless:
            flags.append(f"{name} is on a bound")
    if any(name in harmless for name in on_bound):
        warnings.append(
            "constrained at zero: "
            + ", ".join(n for n in on_bound if n in harmless)
            + " (uncertainties of the other parameters are conditional on this)"
        )

    def unc(i: int) -> float:
        if fixed[i]:
            return 0.0
        if cov is None or not math.isfinite(cov[i, i]):
            return math.nan
        return math.sqrt(cov[i, i])

    verified = spectrum.energy_calibration is not None and spectrum.energy_calibration.is_verified
    peaks: list[FittedPeak] = []
    for j in range(k):
        i_s = layout.sigma_index(j)
        area, mu, sigma = float(theta[j]), float(theta[k + j]), float(theta[i_s])
        amplitude = area / (sigma * _SQRT_2PI)
        amp_unc = math.nan
        if fixed[i_s]:
            amp_unc = unc(j) / (sigma * _SQRT_2PI)
        elif cov is not None and math.isfinite(cov[j, j]) and math.isfinite(cov[i_s, i_s]):
            grad = np.array([1.0 / (sigma * _SQRT_2PI), -area / (sigma * sigma * _SQRT_2PI)])
            sub = cov[np.ix_([j, i_s], [j, i_s])]
            amp_unc = math.sqrt(max(float(grad @ sub @ grad), 0.0))
        energy = None
        if verified:
            assert spectrum.energy_calibration is not None
            energy = float(spectrum.energy_calibration.energy_at(mu))
        peaks.append(
            FittedPeak(
                centroid=mu,
                centroid_unc=unc(k + j),
                area=area,
                area_unc=unc(j),
                sigma=sigma,
                sigma_unc=unc(i_s),
                fwhm=FWHM_PER_SIGMA * sigma,
                fwhm_unc=FWHM_PER_SIGMA * unc(i_s),
                amplitude=amplitude,
                amplitude_unc=amp_unc,
                energy_kev=energy,
                at_bound=tuple(n for n in on_bound if n in (names[j], names[k + j], names[i_s])),
            )
        )
    for j, peak in enumerate(peaks):
        if math.isfinite(peak.significance) and peak.significance < MIN_SIGNIFICANCE:
            warnings.append(
                f"peak {j + 1} is not significant (area / uncertainty = "
                f"{peak.significance:.1f} < {MIN_SIGNIFICANCE:g}): its position and width are "
                "not determined by the data and it must not be reported as a detected peak"
            )
    i_b = layout.background_index
    background = tuple(
        (names[i], float(theta[i]), unc(i)) for i in range(i_b, i_b + layout.n_background)
    )
    step = (float(theta[layout.step_index]), unc(layout.step_index)) if layout.step else None

    if net is not None:
        # joint fit: residual of (gross - alpha*background) against the source model,
        # with the model variance mu_G + alpha^2 beta = 1/w
        residuals = (net - m) * np.sqrt(w)
    elif residual_variance == "information":
        residuals = (data - expected) * np.sqrt(w)
    else:
        residuals = (data - expected) / np.sqrt(np.maximum(expected, _TINY))
    pearson = float(np.sum(residuals**2))
    ndf = int(x.size - int(sol.free.sum()))
    p_value = float(chi2_distribution.sf(pearson, ndf)) if ndf > 0 else math.nan
    gof_method = "chi-square distribution of the Pearson statistic"
    low = int((expected < LOW_EXPECTATION).sum()) if method != "wls" else 0
    if low and method == "poisson" and ndf > 0:
        # chi-square tables fail at low counts: judge the deviance by its exact moments
        mean, var = poisson_deviance_moments(expected)
        z = (float(d.sum()) - (float(mean.sum()) - int(sol.free.sum()))) / math.sqrt(
            float(var.sum())
        )
        p_value = float(normal_distribution.sf(z))
        gof_method = (
            "deviance compared with its exact Poisson mean and variance (normal "
            f"approximation); {low} of {x.size} channels expect fewer than "
            f"{LOW_EXPECTATION:g} counts, so chi-square tables do not apply"
        )
    elif low:
        p_value = math.nan
        gof_method = "none"
        warnings.append(
            f"{low} of {x.size} channels have a fitted gross expectation below "
            f"{LOW_EXPECTATION:g} counts: no goodness-of-fit probability is quoted for the "
            "joint fit; the parameter estimates remain valid"
        )
    if math.isfinite(p_value) and p_value < POOR_FIT_P:
        warnings.append(
            f"the model does not describe the data ({stat.name} {float(d.sum()):.1f}, Pearson "
            f"chi-square {pearson:.1f}, {ndf} degrees of freedom, p < {POOR_FIT_P:g}): "
            "parameters and uncertainties are not reliable"
        )
    if not verified:
        warnings.append("positions and widths are in channels; no verified energy calibration")
    return FitResult(
        method=method,
        first_channel=first_channel,
        last_channel=first_channel + x.size - 1,
        parameter_names=names,
        values=theta,
        covariance=cov,
        peaks=tuple(peaks),
        background=background,
        step_fraction=step,
        channels=x,
        data=data,
        fitted=expected,
        fitted_continuum=continuum,
        residuals=residuals,
        statistic=float(d.sum()),
        statistic_name=stat.name,
        pearson_chi2=pearson,
        ndf=ndf,
        p_value=p_value,
        gof_method=gof_method,
        runs_z=_runs_z(residuals),
        converged=sol.converged,
        iterations=sol.iterations,
        flags=tuple(flags),
        warnings=tuple(warnings),
        spectrum_sha256=spectrum.data_sha256,
        is_synthetic=spectrum.is_synthetic,
        settings=settings,
    )


def _layout(k: int, settings: FitSettings) -> _Layout:
    return _Layout(k, settings.shared_width or k == 1, settings.step, settings.background)


# ---------------------------------------------------------------------- public API
def fit_peaks(
    spectrum: Spectrum,
    first_channel: int,
    last_channel: int,
    centroids: Sequence[float],
    fwhm_channels: float | Sequence[float],
    settings: FitSettings | None = None,
) -> FitResult:
    """Fit ``len(centroids)`` Gaussians plus continuum to channels first..last (inclusive).

    Raises :class:`ProcessingError` for invalid requests. A fit that runs but does not
    converge, or ends on a bound, is returned with ``flags`` set (``result.ok`` False).
    """
    settings = settings or FitSettings()
    _check_inputs(spectrum, centroids, first_channel, last_channel)
    lo, hi = _region(spectrum, first_channel, last_channel)
    layout = _layout(len(centroids), settings)
    if hi - lo <= layout.n_parameters:
        raise ProcessingError(
            f"fit region has {hi - lo} channels for {layout.n_parameters} parameters; widen it"
        )
    method = settings.method
    if method is FitMethod.AUTO:
        method = FitMethod.POISSON if spectrum.is_count_data else FitMethod.WLS
    x = spectrum.channels[lo:hi].astype(np.float64)
    y = spectrum.counts[lo:hi].astype(np.float64)
    extra: list[str] = []
    stat: _Statistic
    if method is FitMethod.POISSON:
        if not spectrum.is_count_data:
            raise ProcessingError(
                "the Poisson likelihood applies to measured counts only, not to "
                f"{spectrum.kind.value} data (which may be negative). Fit the gross spectrum, "
                "use the joint gross+background fit, or use weighted least squares"
            )
        stat = _Poisson(y)
    else:
        variance = np.asarray(spectrum.variance_array()[lo:hi], dtype=np.float64)
        if np.any(variance <= 0.0):
            raise ProcessingError(
                f"{int((variance <= 0.0).sum())} channels of the region have zero variance, so "
                "least-squares weights are undefined. Use the Poisson likelihood on count "
                "data or the joint gross+background fit"
            )
        if spectrum.is_count_data:
            extra.append(
                "weighted least squares on count data uses 1/n weights, which bias areas "
                "low when counts are small; the Poisson likelihood is preferred"
            )
        else:
            extra.append(
                "per-channel variances only: a correlated uncertainty of the background "
                "scaling factor is not included"
            )
        stat = _WeightedLeastSquares(y, variance)
    fwhm = _widths(fwhm_channels, len(centroids))
    non_negative = method is FitMethod.POISSON
    theta0, lower, upper = _start_and_bounds(x, y, centroids, fwhm, layout, non_negative)
    _hold_fixed(theta0, lower, upper, layout, settings)
    sol = _minimise(x, layout, stat, theta0, lower, upper, settings)
    return _build_result(
        method=method.value,
        spectrum=spectrum,
        first_channel=first_channel,
        x=x,
        data=y,
        stat=stat,
        layout=layout,
        sol=sol,
        lower=lower,
        upper=upper,
        residual_variance="information" if method is FitMethod.WLS else "expectation",
        settings=settings,
        extra_warnings=extra,
    )


def fit_peaks_joint(
    gross: Spectrum,
    background: Spectrum,
    first_channel: int,
    last_channel: int,
    centroids: Sequence[float],
    fwhm_channels: float | Sequence[float],
    settings: FitSettings | None = None,
) -> FitResult:
    """Joint Poisson fit of a gross spectrum and an independent, designated background.

    The peak model (with its continuum, here the *source-induced* continuum) describes the
    source contribution to the gross spectrum. The same validity rules as for background
    subtraction apply (same detector and gain, independent acquisitions, live times).
    In ``FitResult``: ``data`` are the gross counts, ``fitted`` their expectation
    (ambient background + source), and ``residuals`` are those of gross - alpha*background
    with the model variance.
    """
    settings = settings or FitSettings()
    compat = check_compatibility(gross, background)
    if not compat.ok:
        raise ProcessingError("joint fit refused: " + "; ".join(compat.errors))
    assert gross.live_time_s is not None and background.live_time_s is not None
    _check_inputs(gross, centroids, first_channel, last_channel)
    lo, hi = _region(gross, first_channel, last_channel)
    layout = _layout(len(centroids), settings)
    if hi - lo <= layout.n_parameters:
        raise ProcessingError(
            f"fit region has {hi - lo} channels for {layout.n_parameters} parameters; widen it"
        )
    alpha = gross.live_time_s / background.live_time_s
    x = gross.channels[lo:hi].astype(np.float64)
    g = gross.counts[lo:hi].astype(np.float64)
    b = background.counts[lo:hi].astype(np.float64)
    stat = _JointPoisson(g, b, alpha)
    fwhm = _widths(fwhm_channels, len(centroids))
    net = g - alpha * b
    theta0, lower, upper = _start_and_bounds(
        x, net, centroids, fwhm, layout, True, continuum_non_negative=False
    )
    _hold_fixed(theta0, lower, upper, layout, settings)
    sol = _minimise(x, layout, stat, theta0, lower, upper, settings)
    extra = [*compat.warnings]
    mean_b = float(b.mean())
    if mean_b < JOINT_LOW_BACKGROUND:
        extra.append(
            f"the background measurement has only {mean_b:.2g} counts per channel in this "
            "region. With one nuisance parameter per channel the profile likelihood then "
            "understates the uncertainties (in synthetic tests 1-sigma intervals of "
            "position and width covered only about 50-60 %); a longer background "
            "measurement or a fit of the gross spectrum alone is more reliable"
        )
    return _build_result(
        method="joint-poisson",
        spectrum=gross,
        first_channel=first_channel,
        x=x,
        data=g,
        stat=stat,
        layout=layout,
        sol=sol,
        lower=lower,
        upper=upper,
        residual_variance="joint",
        settings=settings,
        extra_warnings=extra,
        net=net,
    )


def fit_region_for(
    spectrum: Spectrum,
    centroids: Sequence[float],
    fwhm_channels: float,
    half_width_fwhm: float = 3.0,
) -> tuple[int, int]:
    """Channel range covering the peaks plus ``half_width_fwhm`` FWHM on either side."""
    if not centroids:
        raise ProcessingError("at least one peak position is required")
    margin = half_width_fwhm * fwhm_channels
    first = max(spectrum.first_channel, math.floor(min(centroids) - margin))
    last = min(spectrum.first_channel + spectrum.n_channels - 1, math.ceil(max(centroids) + margin))
    return first, last


@dataclass(frozen=True, slots=True)
class CandidateFit:
    """Outcome of fitting one overlap group of a peak search."""

    group: int
    result: FitResult | None
    error: str | None  # reason when no fit could be attempted


def fit_candidates(
    spectrum: Spectrum,
    search: PeakSearchResult,
    settings: FitSettings | None = None,
    half_width_fwhm: float = 3.0,
) -> tuple[CandidateFit, ...]:
    """Fit every overlap group of a peak search as one multiplet."""
    if search.spectrum_sha256 != spectrum.data_sha256:
        raise ProcessingError("the peak search belongs to a different spectrum")
    out: list[CandidateFit] = []
    for group in search.groups():
        centroids = [c.channel for c in group]
        widths = [c.fwhm_guess for c in group]
        try:
            first, last = fit_region_for(spectrum, centroids, max(widths), half_width_fwhm)
            result = fit_peaks(spectrum, first, last, centroids, widths, settings)
        except ProcessingError as exc:
            out.append(CandidateFit(group[0].group, None, str(exc)))
        else:
            out.append(CandidateFit(group[0].group, result, None))
    return tuple(out)


# ---------------------------------------------------------------------- multiplets
@dataclass(frozen=True, slots=True)
class MultipletEvidence:
    """Comparison of a k-component fit with the best (k-1)-component fit of the same data."""

    n_components: int
    statistic_full: float
    statistic_reduced: float
    removed_parameters: int
    delta_aic: float  # AIC(reduced) - AIC(full); > 0 favours the extra component
    delta_bic: float
    separations_fwhm: tuple[float, ...]  # neighbouring centroid distances / mean FWHM
    area_correlations: tuple[float, ...]  # correlation of neighbouring areas
    supported: bool
    statement: str


def multiplet_evidence(
    spectrum: Spectrum,
    first_channel: int,
    last_channel: int,
    centroids: Sequence[float],
    fwhm_channels: float | Sequence[float],
    settings: FitSettings | None = None,
) -> MultipletEvidence:
    """Is the last component of a multiplet demanded by the data?

    Fits all k components, then every model with one component removed, and compares the
    best of those with the full model by the Akaike and Bayesian information criteria
    (the fit statistic is -2 log L up to a constant). The likelihood-ratio statistic does
    not follow a chi-square distribution here (the removed area lies on the boundary of
    its range and its position is then undefined), so no p-value is quoted. ``supported``
    requires delta BIC >= 10 and a full fit that has no flags and is statistically
    acceptable; convergence alone is never taken as evidence that components are resolved.
    """
    settings = settings or FitSettings()
    k = len(centroids)
    if k < 2:
        raise ProcessingError("multiplet evidence needs at least two components")
    widths = _widths(fwhm_channels, k)
    full = fit_peaks(spectrum, first_channel, last_channel, centroids, widths, settings)
    best: FitResult | None = None
    for keep in combinations(range(k), k - 1):
        trial = fit_peaks(
            spectrum,
            first_channel,
            last_channel,
            [centroids[i] for i in keep],
            [widths[i] for i in keep],
            settings,
        )
        if trial.converged and (best is None or trial.statistic < best.statistic):
            best = trial
    order = np.argsort([p.centroid for p in full.peaks])
    ordered = [full.peaks[int(i)] for i in order]
    separations = tuple(
        (b.centroid - a.centroid) / (0.5 * (a.fwhm + b.fwhm)) for a, b in pairwise(ordered)
    )
    correlations = tuple(
        full.correlation(f"area{int(i) + 1}", f"area{int(j) + 1}") for i, j in pairwise(order)
    )
    if best is None:
        return MultipletEvidence(
            k, full.statistic, math.nan, 0, math.nan, math.nan, separations, correlations,
            False, "the reduced model could not be fitted; no comparison is possible",
        )  # fmt: skip
    removed = len(full.parameter_names) - len(best.parameter_names)
    delta = best.statistic - full.statistic
    n = full.channels.size
    delta_aic = delta - 2.0 * removed
    delta_bic = delta - removed * math.log(n)
    supported = full.ok and not full.poor_fit and delta_bic >= STRONG_EVIDENCE_BIC
    if full.ok and full.poor_fit:
        statement = (
            f"the {k}-component model does not describe the data (chi-square "
            f"{full.pearson_chi2:.1f} for {full.ndf} degrees of freedom); no conclusion about "
            "the number of components can be drawn from it"
        )
    elif supported:
        statement = (
            f"{k} components are supported over {k - 1} (ΔBIC = {delta_bic:.1f}); this is "
            "statistical support for the model, not proof that the lines are resolved"
        )
    elif not full.ok:
        statement = (
            f"the {k}-component fit is flagged ({'; '.join(full.flags)}); its components "
            "must not be reported as separate peaks"
        )
    else:
        statement = (
            f"the data do not require {k} components (ΔBIC = {delta_bic:.1f} < "
            f"{STRONG_EVIDENCE_BIC:g}); report the {k - 1}-component result"
        )
    return MultipletEvidence(
        k, full.statistic, best.statistic, removed, delta_aic, delta_bic, separations,
        correlations, supported, statement,
    )  # fmt: skip
