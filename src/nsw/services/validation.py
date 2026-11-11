"""Use case: Monte Carlo validation of peak search and peak fitting on synthetic spectra.

Everything here is *software verification against a simulated detector*. It says how
the estimators behave when the model is exactly right; it is not a validation of any
real detector.

Definitions (T trials; theta the true value, est_t the estimate and u_t the reported
standard uncertainty of trial t; sums over the n_ok trials whose fit is ``ok``)
--------------------------------------------------------------------------------
bias              mean(est_t - theta)
bias standard error   sd(est_t) / sqrt(n_ok)
relative bias     bias / theta
precision         sd(est_t)  (sample standard deviation, n_ok - 1 in the denominator)
pull              (est_t - theta) / u_t ; ideal: mean 0, standard deviation 1
coverage(k)       fraction of trials with |est_t - theta| <= k u_t ; ideal 0.6827 (k = 1)
                  and 0.9545 (k = 2)
failure rate      (T - n_ok) / T  (exception, non-convergence, parameter on a bound or
                  unusable covariance)
false-alarm rate  fraction of valid fits of a correct model with p-value < 0.05
                  (ideal 0.05)
detection probability   fraction of trials with a candidate within one FWHM of the true
                  position
false detections  candidates per 1000 channels in spectra that contain no peak

The true area is the generator's *expected* peak count (rate x live time), i.e. the
parameter the estimator targets, not the realised number of counts of a trial.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from nsw.analysis.fitting import (
    FWHM_PER_SIGMA,
    FitMethod,
    FitResult,
    FitSettings,
    fit_peaks,
    fit_peaks_joint,
)
from nsw.analysis.peaks import PeakSearchSettings, find_peak_candidates
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum
from nsw.detectors.profiles import DetectorType
from nsw.simulation.generator import (
    BackgroundModel,
    ResolutionModel,
    SyntheticLine,
    SyntheticSpec,
    generate,
)

N_CHANNELS = 400
TRUE_CHANNEL = 200.0
GAIN_KEV = 0.25
OFFSET_KEV = 0.5
LIVE_S = 1000.0
ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True, slots=True)
class FitStudyCase:
    name: str
    area: float  # expected peak counts
    background_per_channel: float  # expected continuum counts per channel
    fwhm_channels: float
    method: str = "poisson"  # "poisson", "wls" or "joint"
    background_live_ratio: float = 1.0  # joint only: background live time / gross live time
    half_width_fwhm: float = 3.0
    fixed_shape: bool = False  # hold position and width at their true values

    def __post_init__(self) -> None:
        if self.method not in ("poisson", "wls", "joint"):
            raise ProcessingError(f"unknown study method {self.method!r}")


@dataclass(frozen=True, slots=True)
class ParameterStats:
    truth: float
    bias: float
    bias_se: float
    sd: float
    mean_uncertainty: float
    pull_mean: float
    pull_sd: float
    coverage_1: float
    coverage_2: float

    @property
    def relative_bias(self) -> float:
        return self.bias / self.truth


@dataclass(frozen=True)
class FitStudyResult:
    case: FitStudyCase
    n_trials: int
    n_ok: int
    area: ParameterStats
    centroid: ParameterStats
    sigma: ParameterStats
    false_alarm_rate: float  # NaN if no p-values are available

    @property
    def failure_rate(self) -> float:
        return (self.n_trials - self.n_ok) / self.n_trials


@dataclass(frozen=True, slots=True)
class DetectionStudyResult:
    area: float
    background_per_channel: float
    fwhm_channels: float
    threshold: float
    n_trials: int
    detection_probability: float
    detection_se: float
    false_per_1000_channels: float
    false_trials: int  # background-only spectra searched


def _spec(
    seed: int, area: float, background: float, fwhm_channels: float, *, role: str = "source"
) -> SyntheticSpec:
    energy = OFFSET_KEV + GAIN_KEV * TRUE_CHANNEL
    lines: tuple[SyntheticLine, ...] = ()
    if role == "source" and area > 0.0:
        lines = (SyntheticLine(energy_kev=energy, rate_cps=area / LIVE_S),)
    return SyntheticSpec(
        synthetic_id=f"mc-{role}-{seed}",
        detector_type=DetectorType.HPGE,
        detector_id="synthetic-mc",
        n_channels=N_CHANNELS,
        energy_offset_kev=OFFSET_KEV,
        energy_gain_kev_per_channel=GAIN_KEV,
        resolution=ResolutionModel(a=(fwhm_channels * GAIN_KEV) ** 2, b=0.0),
        lines=lines,
        background=BackgroundModel(constant_cps_per_channel=background / LIVE_S),
        live_time_s=LIVE_S,
        real_time_s=LIVE_S,
        seed=seed,
        role="background" if role == "background" else "source",
    )


def _background_spec(seed: int, background: float, ratio: float) -> SyntheticSpec:
    base = _spec(seed, 0.0, background, 6.0, role="background")
    return base.model_copy(update={"live_time_s": LIVE_S * ratio, "real_time_s": LIVE_S * ratio})


def _stats(truth: float, est: list[float], unc: list[float]) -> ParameterStats:
    e = np.asarray(est) - truth
    u = np.asarray(unc)
    n = e.size
    if n < 2 or np.any(u <= 0.0):  # too few fits, or the parameter was held fixed
        nan = math.nan
        return ParameterStats(truth, nan, nan, nan, nan, nan, nan, nan, nan)
    pull = e / u
    sd = float(e.std(ddof=1))
    return ParameterStats(
        truth=truth,
        bias=float(e.mean()),
        bias_se=sd / math.sqrt(n),
        sd=sd,
        mean_uncertainty=float(u.mean()),
        pull_mean=float(pull.mean()),
        pull_sd=float(pull.std(ddof=1)),
        coverage_1=float(np.mean(np.abs(pull) <= 1.0)),
        coverage_2=float(np.mean(np.abs(pull) <= 2.0)),
    )


def run_fit_study(
    case: FitStudyCase,
    n_trials: int = 400,
    seed: int = 10_000,
    progress: ProgressCallback | None = None,
) -> FitStudyResult:
    """Fit ``n_trials`` independent synthetic spectra of one configuration."""
    w = case.fwhm_channels
    first = int(TRUE_CHANNEL - case.half_width_fwhm * w)
    last = int(TRUE_CHANNEL + case.half_width_fwhm * w)
    true_sigma = w / FWHM_PER_SIGMA
    est: dict[str, list[float]] = {"area": [], "centroid": [], "sigma": []}
    unc: dict[str, list[float]] = {"area": [], "centroid": [], "sigma": []}
    p_values: list[float] = []
    for t in range(n_trials):
        if progress is not None and t % 25 == 0:
            progress(t, n_trials, case.name)
        gross = generate(_spec(seed + t, case.area, case.background_per_channel, w)).spectrum
        result: FitResult
        try:
            if case.method == "joint":
                background = generate(
                    _background_spec(
                        seed + 5_000_000 + t,
                        case.background_per_channel,
                        case.background_live_ratio,
                    )
                ).spectrum
                result = fit_peaks_joint(
                    gross,
                    background,
                    first,
                    last,
                    [TRUE_CHANNEL],
                    w,
                    FitSettings(fixed_centroids=case.fixed_shape, fixed_width=case.fixed_shape),
                )
            else:
                method = FitMethod.POISSON if case.method == "poisson" else FitMethod.WLS
                result = fit_peaks(
                    gross,
                    first,
                    last,
                    [TRUE_CHANNEL],
                    w,
                    FitSettings(
                        method=method,
                        fixed_centroids=case.fixed_shape,
                        fixed_width=case.fixed_shape,
                    ),
                )
        except ProcessingError:
            continue
        if not result.ok:
            continue
        p = result.peaks[0]
        for key, value, u in (
            ("area", p.area, p.area_unc),
            ("centroid", p.centroid, p.centroid_unc),
            ("sigma", p.sigma, p.sigma_unc),
        ):
            est[key].append(value)
            unc[key].append(u)
        if math.isfinite(result.p_value):
            p_values.append(result.p_value)
    return FitStudyResult(
        case=case,
        n_trials=n_trials,
        n_ok=len(est["area"]),
        area=_stats(case.area, est["area"], unc["area"]),
        centroid=_stats(TRUE_CHANNEL, est["centroid"], unc["centroid"]),
        sigma=_stats(true_sigma, est["sigma"], unc["sigma"]),
        false_alarm_rate=float(np.mean(np.asarray(p_values) < 0.05)) if p_values else math.nan,
    )


def run_detection_study(
    area: float,
    background_per_channel: float,
    fwhm_channels: float,
    scales: tuple[float, ...],
    threshold: float = 4.0,
    n_trials: int = 400,
    seed: int = 20_000,
) -> DetectionStudyResult:
    """Detection probability of one peak and false detections in peak-free spectra."""
    settings = PeakSearchSettings(fwhm_channels=scales, threshold=threshold)
    found = 0
    false = 0
    for t in range(n_trials):
        source: Spectrum = generate(
            _spec(seed + t, area, background_per_channel, fwhm_channels)
        ).spectrum
        candidates = find_peak_candidates(source, settings).candidates
        if any(abs(c.channel - TRUE_CHANNEL) <= fwhm_channels for c in candidates):
            found += 1
        blank = generate(
            _spec(seed + 7_000_000 + t, 0.0, background_per_channel, fwhm_channels)
        ).spectrum
        false += len(find_peak_candidates(blank, settings).candidates)
    p = found / n_trials
    return DetectionStudyResult(
        area=area,
        background_per_channel=background_per_channel,
        fwhm_channels=fwhm_channels,
        threshold=threshold,
        n_trials=n_trials,
        detection_probability=p,
        detection_se=math.sqrt(max(p * (1.0 - p), 0.0) / n_trials),
        false_per_1000_channels=false / (n_trials * N_CHANNELS / 1000.0),
        false_trials=n_trials,
    )


STANDARD_FIT_CASES: tuple[FitStudyCase, ...] = (
    FitStudyCase("HPGe-like, high count", 20000.0, 50.0, 6.0),
    FitStudyCase("HPGe-like, medium count", 500.0, 20.0, 6.0),
    FitStudyCase("HPGe-like, low count", 30.0, 0.6, 6.0),
    FitStudyCase("HPGe-like, low count, nearly empty continuum", 30.0, 0.05, 6.0),
    FitStudyCase("NaI-like broad peak, high count", 20000.0, 200.0, 15.0),
    FitStudyCase("NaI-like broad peak, low count", 60.0, 2.0, 15.0),
    FitStudyCase("background-dominated peak", 300.0, 400.0, 6.0),
    FitStudyCase(
        "background-dominated peak, position and width fixed", 300.0, 400.0, 6.0, fixed_shape=True
    ),
    FitStudyCase("low count, position and width fixed", 30.0, 0.6, 6.0, fixed_shape=True),
    FitStudyCase("weighted least squares, high count", 20000.0, 50.0, 6.0, method="wls"),
    FitStudyCase("weighted least squares, 8 counts/channel", 200.0, 8.0, 6.0, method="wls"),
    FitStudyCase("Poisson likelihood, 8 counts/channel", 200.0, 8.0, 6.0),
    FitStudyCase("joint gross+background, medium count", 500.0, 20.0, 6.0, method="joint"),
    FitStudyCase("joint, low count, equal live times", 30.0, 0.6, 6.0, method="joint"),
    FitStudyCase(
        "joint, low count, background measured 10x longer",
        30.0,
        0.6,
        6.0,
        method="joint",
        background_live_ratio=10.0,
    ),
)

# (area, background per channel, FWHM, scales)
STANDARD_DETECTION_CASES: tuple[tuple[float, float, float, tuple[float, ...]], ...] = (
    (20.0, 0.6, 6.0, (6.0, 12.0)),
    (30.0, 0.6, 6.0, (6.0, 12.0)),
    (50.0, 0.6, 6.0, (6.0, 12.0)),
    (100.0, 20.0, 6.0, (6.0, 12.0)),
    (200.0, 20.0, 6.0, (6.0, 12.0)),
    (300.0, 400.0, 6.0, (6.0, 12.0)),
    (600.0, 400.0, 6.0, (6.0, 12.0)),
    (200.0, 20.0, 15.0, (15.0, 30.0)),
    (400.0, 20.0, 15.0, (15.0, 30.0)),
)
