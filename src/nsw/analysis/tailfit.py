"""Optional HPGe low-energy tail: Gaussian + exponentially modified Gaussian (EMG) tail.

Design: docs/design/HPGE_PEAK_TAIL_DESIGN.md (owner-approved 2026-11-12). Per peak j, in channel
space and integrated over each channel like the Gaussian model of ``analysis.fitting``:

    m(x) = continuum(x) + Σ_j N_j [ (1 − η) G(x; μ_j, σ) + η T(x; μ_j, σ, β) ]

T is the low-side EMG of unit area (X = Y − E, Y ~ N(μ, σ²), E ~ exponential with mean
β). Its CDF is Φ(u/σ) + exp(u/β + σ²/(2β²) + log Φ(−u/σ − σ/β)), u = x − μ, evaluated
with ``log_ndtr`` so the product of a large exponential and a small normal tail never
overflows. N_j is the total full-energy area (Gaussian plus tail). σ, η and β are shared
by the peaks of a region; the continuum is linear (values at both ends, ≥ 0).

Estimator: Poisson maximum likelihood (deviance minimised with bounds: N ≥ 0,
0 ≤ η ≤ 0.5, 0.1 ≤ β ≤ 20σ₀). Covariance: inverse of the expected Fisher information
JᵀWJ (W = 1/m) with a central-difference Jacobian. Starts from the Gaussian fit of the
same region; starting values are recorded.

Model selection (``tail_test``): Δ = D_gauss − D_tail. η = 0 lies on the boundary and β
is undefined there, so Δ has no chi-square reference; the threshold is the 99 % quantile
of Δ in a parametric bootstrap from the Gaussian fit. The tail is preferred only above it.
This module never replaces the Gaussian model by itself.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize
from scipy.special import log_ndtr, ndtr, xlogy

from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum

Float = NDArray[np.float64]
ETA_MAX = 0.5
BETA_MIN = 0.1
BETA_MAX_SIGMA = 20.0
ALGORITHM_VERSION = "tailfit/1"


def emg_low_cdf(x: Float, mu: float, sigma: float, beta: float) -> Float:
    u = x - mu
    log_term = u / beta + sigma**2 / (2.0 * beta**2) + log_ndtr(-u / sigma - sigma / beta)
    out: Float = ndtr(u / sigma) + np.exp(np.minimum(log_term, 700.0))
    return out


def peak_shape(x: Float, mu: float, sigma: float, eta: float, beta: float) -> Float:
    """Unit-area bin contents of (1 − η)·Gaussian + η·low-side EMG at channels x."""
    up, lo = x + 0.5, x - 0.5
    gauss = ndtr((up - mu) / sigma) - ndtr((lo - mu) / sigma)
    if eta == 0.0:
        out: Float = gauss
        return out
    tail = emg_low_cdf(up, mu, sigma, beta) - emg_low_cdf(lo, mu, sigma, beta)
    return np.asarray((1.0 - eta) * gauss + eta * tail, dtype=np.float64)


@dataclass(frozen=True)
class TailFit:
    channels: Float
    data: Float
    fitted: Float
    areas: tuple[float, ...]
    area_uncs: tuple[float, ...]
    centroids: tuple[float, ...]
    sigma: float
    eta: float
    eta_unc: float
    beta: float
    beta_unc: float
    deviance: float
    ndf: int
    converged: bool
    covariance: Float
    start: tuple[float, ...] = field(default=())
    algorithm: str = ALGORITHM_VERSION

    @property
    def fwhm(self) -> float:
        return 2.0 * math.sqrt(2.0 * math.log(2.0)) * self.sigma


def _deviance(n: Float, m: Float) -> float:
    m = np.maximum(m, 1e-300)
    return float(2.0 * np.sum(m - n + xlogy(n, n) - xlogy(n, m)))


def _model(p: Float, x: Float, k: int) -> Float:
    areas, mus = p[:k], p[k : 2 * k]
    sigma, eta, beta, b_left, b_right = p[2 * k : 2 * k + 5]
    t = (x - x[0]) / max(x[-1] - x[0], 1.0)
    m = b_left * (1.0 - t) + b_right * t
    for a, mu in zip(areas, mus, strict=True):
        m = m + a * peak_shape(x, float(mu), float(sigma), float(eta), float(beta))
    return np.asarray(m, dtype=np.float64)


def fit_tailed(
    spectrum: Spectrum,
    first_channel: int,
    last_channel: int,
    centroids: Sequence[float],
    fwhm: float,
    areas: Sequence[float] | None = None,
    *,
    eta_start: float = 0.05,
    beta_start_sigma: float = 1.0,
) -> TailFit:
    """Poisson fit of Gaussian + low-side tail peaks on a linear continuum."""
    if not spectrum.is_count_data:
        raise ProcessingError("the tail model is fitted to count data only (Poisson)")
    lo = first_channel - spectrum.first_channel
    hi = last_channel - spectrum.first_channel + 1
    if not (0 <= lo < hi <= spectrum.n_channels) or hi - lo < 8:
        raise ProcessingError("the region must lie inside the spectrum and span ≥ 8 channels")
    k = len(centroids)
    if k == 0:
        raise ProcessingError("at least one peak position is required")
    x = np.arange(first_channel, last_channel + 1, dtype=np.float64)
    n = np.asarray(spectrum.counts[lo:hi], dtype=np.float64)
    sigma0 = fwhm / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    edge = max(2, (hi - lo) // 10)
    b_l, b_r = float(n[:edge].mean()), float(n[-edge:].mean())
    if areas is None:
        net = max(float(n.sum()) - 0.5 * (b_l + b_r) * n.size, 1.0)
        areas = [net / k] * k
    p0 = np.array(
        [*areas, *centroids, sigma0, eta_start, beta_start_sigma * sigma0, b_l, b_r],
        dtype=np.float64,
    )
    bounds = (
        [(0.0, None)] * k
        + [(c - 2.0 * fwhm, c + 2.0 * fwhm) for c in centroids]
        + [(0.3 * sigma0, 3.0 * sigma0), (0.0, ETA_MAX)]
        + [(BETA_MIN, BETA_MAX_SIGMA * sigma0), (0.0, None), (0.0, None)]
    )

    def objective(p: Float) -> float:
        return _deviance(n, _model(p, x, k))

    res = minimize(
        objective, p0, method="L-BFGS-B", bounds=bounds, options={"maxiter": 2000, "ftol": 1e-12}
    )
    p = np.asarray(res.x, dtype=np.float64)
    m = _model(p, x, k)
    # expected Fisher information with a central-difference Jacobian
    jac = np.empty((x.size, p.size))
    for i in range(p.size):
        h = 1e-6 * max(abs(p[i]), 1e-3)
        dp = np.zeros_like(p)
        dp[i] = h
        jac[:, i] = (_model(p + dp, x, k) - _model(p - dp, x, k)) / (2.0 * h)
    info = jac.T @ (jac / np.maximum(m, 1e-12)[:, None])
    try:
        cov = np.linalg.inv(info)
    except np.linalg.LinAlgError:
        cov = np.full((p.size, p.size), np.nan)
    unc = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    return TailFit(
        channels=x,
        data=n,
        fitted=m,
        areas=tuple(float(v) for v in p[:k]),
        area_uncs=tuple(float(v) for v in unc[:k]),
        centroids=tuple(float(v) for v in p[k : 2 * k]),
        sigma=float(p[2 * k]),
        eta=float(p[2 * k + 1]),
        eta_unc=float(unc[2 * k + 1]),
        beta=float(p[2 * k + 2]),
        beta_unc=float(unc[2 * k + 2]),
        deviance=_deviance(n, m),
        ndf=int(x.size - p.size),
        converged=bool(res.success),
        covariance=cov,
        start=tuple(float(v) for v in p0),
    )


@dataclass(frozen=True)
class TailTest:
    gaussian_deviance: float
    tail_deviance: float
    delta: float
    threshold: float  # bootstrap 99 % quantile of delta under the Gaussian model
    replicas: int
    preferred: str  # "gaussian" or "tailed"
    tail: TailFit

    @property
    def reason(self) -> str:
        verdict = (
            "the tail improves the fit beyond what the Gaussian model produces by chance"
            if self.preferred == "tailed"
            else "the improvement is within what the Gaussian model produces by chance"
        )
        return (
            f"Δdeviance {self.delta:.1f} vs bootstrap 99 % threshold {self.threshold:.1f} "
            f"({self.replicas} replicas): {verdict}"
        )


def tail_test(
    spectrum: Spectrum,
    first_channel: int,
    last_channel: int,
    gaussian_fitted: Float,
    gaussian_deviance: float,
    centroids: Sequence[float],
    fwhm: float,
    areas: Sequence[float],
    replicas: int = 100,
    seed: int = 20261112,
) -> TailTest:
    """Compare the Gaussian fit with the tailed model; bootstrap-calibrated threshold."""
    from dataclasses import replace

    from nsw.analysis.fitting import BackgroundShape, FitMethod, FitSettings, fit_peaks

    tail = fit_tailed(spectrum, first_channel, last_channel, centroids, fwhm, areas)
    delta = gaussian_deviance - tail.deviance
    rng = np.random.default_rng(seed)
    expectation = np.asarray(gaussian_fitted, dtype=np.float64)
    lo = first_channel - spectrum.first_channel
    deltas = []
    settings = FitSettings(method=FitMethod.POISSON, background=BackgroundShape.LINEAR)
    for _ in range(replicas):
        counts = np.array(spectrum.counts, copy=True)
        counts[lo : lo + expectation.size] = rng.poisson(expectation)
        sim = replace(spectrum, counts=counts, variance=None)
        try:
            g = fit_peaks(sim, first_channel, last_channel, centroids, fwhm, settings)
            t = fit_tailed(sim, first_channel, last_channel, centroids, fwhm, areas)
        except ProcessingError:
            continue
        deltas.append(g.statistic - t.deviance)
    if len(deltas) < 20:
        raise ProcessingError("too few bootstrap replicas converged to set a threshold")
    threshold = float(np.quantile(deltas, 0.99))
    preferred = "tailed" if delta > threshold else "gaussian"
    return TailTest(
        gaussian_deviance, tail.deviance, delta, threshold, len(deltas), preferred, tail
    )
