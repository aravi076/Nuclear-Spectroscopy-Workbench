"""Automatic peak search in channel space.

Method
------
The spectrum y is correlated with a zero-area, symmetric kernel: the negative second
derivative of a Gaussian of standard deviation s (the detection *scale*),

    k_j = (1 - j^2/s^2) exp(-j^2 / (2 s^2)),   j = -h..h,   then  k <- k - mean(k).

    R_i = sum_j k_j y_{i+j}                (filter response)
    V_i = sum_j k_j^2 var(y_{i+j})         (its variance for independent channels)
    S_i = R_i / sqrt(V_i)                  (significance)

Because the kernel has zero area and is symmetric, R vanishes for any constant or
linear continuum: the search is background-aware without estimating a baseline. A
candidate is a local maximum of S above the threshold. Several scales may be searched;
where candidates of different scales coincide the most significant one is kept.

For a Gaussian peak of area N and standard deviation p on a locally linear continuum,

    R(s) = N * s^3 / (s^2 + p^2)^(3/2)          (continuous approximation)

so the responses at two scales s1 < s2 give baseline-free estimates of the width,
p^2 = (s2^2 - rho s1^2) / (rho - 1) with rho = (R1/R2)^(2/3) (s2/s1)^2, and of the area.
These are *estimates* for display and for starting a fit; quantitative values come from
``nsw.analysis.fitting``.

On a continuum-dominated background V grows like s, so S is largest for s = sqrt(5) p:
the scale that detects a peak best is about 2.2 times wider than the peak, and the
detecting scale is therefore *not* a width estimate. A coarse ladder of scales is enough.

Limitations (see docs/scientific_methodology.md, section 7)
----------------------------------------------------------
* V uses the per-channel variance estimate of the spectrum (observed counts for count
  data). In regions with very few counts S is not normally distributed, and where all
  channels under the kernel are empty S is defined as zero. No pseudo-counts are added.
* Any sharp curvature of the continuum (a Compton edge, a backscatter bump) produces a
  response. A candidate is therefore not a confirmed full-energy peak.
* Two lines closer than about one FWHM give a single candidate; the search cannot show
  that a candidate is a single line.
* Positions are channel numbers. An energy is attached only when the spectrum carries a
  *verified* calibration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.signal import find_peaks

from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum

FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
KERNEL_HALF_WIDTH_SIGMA = 4.0
# Scale of maximum significance / peak sigma on a continuum-dominated background.
BEST_SCALE_RATIO = math.sqrt(5.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


@dataclass(frozen=True, slots=True)
class PeakSearchSettings:
    """Adjustable parameters of the peak search (all widths in channels)."""

    fwhm_channels: tuple[float, ...]  # detection scales: expected peak FWHM values
    threshold: float = 4.0  # minimum significance S
    min_separation_fwhm: float = 1.0  # candidates closer than this (x scale) are merged
    overlap_fwhm: float = 3.0  # candidates closer than this (x FWHM) form one fit group
    first_channel: int | None = None  # search region (inclusive, 1-based); None = all
    last_channel: int | None = None

    def __post_init__(self) -> None:
        if not self.fwhm_channels:
            raise ProcessingError("at least one detection scale (FWHM in channels) is required")
        for w in self.fwhm_channels:
            if not math.isfinite(w) or w < 2.0:
                raise ProcessingError(
                    f"detection scale {w!r} is not usable: the FWHM must be at least 2 channels"
                )
        if not math.isfinite(self.threshold) or self.threshold <= 0.0:
            raise ProcessingError("the significance threshold must be > 0")
        if self.min_separation_fwhm <= 0.0 or self.overlap_fwhm <= 0.0:
            raise ProcessingError("separation parameters must be > 0")


@dataclass(frozen=True, slots=True)
class PeakCandidate:
    """One peak candidate. Every number except ``significance`` is an estimate."""

    channel: float  # interpolated position (1-based channel coordinate)
    channel_uncertainty: float  # standard uncertainty of the position (statistical)
    significance: float  # S at the detecting scale (signal-to-noise ratio)
    scale_fwhm: float  # detection scale that gave the largest significance
    fwhm_estimate: float | None  # two-scale width estimate, channels; None if not defined
    area_estimate: float  # estimated net area, counts
    prominence: float  # estimated net height above the local continuum, counts/channel
    group: int  # candidates sharing a group overlap and must be fitted together
    energy_kev: float | None = None  # only with a verified calibration

    @property
    def fwhm_guess(self) -> float:
        """Width to start a fit with: the estimate, else the detecting scale / sqrt(5)."""
        if self.fwhm_estimate is not None:
            return self.fwhm_estimate
        return self.scale_fwhm / BEST_SCALE_RATIO


@dataclass(frozen=True)
class PeakSearchResult:
    candidates: tuple[PeakCandidate, ...]
    settings: PeakSearchSettings
    spectrum_sha256: str
    first_channel: int  # region actually searched
    last_channel: int
    warnings: tuple[str, ...]

    def groups(self) -> tuple[tuple[PeakCandidate, ...], ...]:
        out: dict[int, list[PeakCandidate]] = {}
        for c in self.candidates:
            out.setdefault(c.group, []).append(c)
        return tuple(tuple(v) for _, v in sorted(out.items()))


# ---------------------------------------------------------------------- filter
def search_kernel(sigma: float) -> NDArray[np.float64]:
    """Zero-area negative-second-derivative-of-Gaussian kernel for standard deviation sigma."""
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ProcessingError("kernel sigma must be > 0")
    half = max(2, math.ceil(KERNEL_HALF_WIDTH_SIGMA * sigma))
    j = np.arange(-half, half + 1, dtype=np.float64)
    k = (1.0 - (j / sigma) ** 2) * np.exp(-0.5 * (j / sigma) ** 2)
    return np.asarray(k - k.mean(), dtype=np.float64)


def filter_response(
    values: NDArray[np.float64], variance: NDArray[np.float64], sigma: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Response R and its variance V. Channels closer to an end than the kernel half-width
    cannot be evaluated; R and V are zero there."""
    k = search_kernel(sigma)
    half = (k.size - 1) // 2
    r = np.zeros(values.size, dtype=np.float64)
    v = np.zeros(values.size, dtype=np.float64)
    if values.size >= k.size:
        r[half : values.size - half] = np.correlate(values, k, mode="valid")
        v[half : values.size - half] = np.correlate(variance, k * k, mode="valid")
    return r, v


def significance(
    values: NDArray[np.float64], variance: NDArray[np.float64], sigma: float
) -> NDArray[np.float64]:
    r, v = filter_response(values, variance, sigma)
    out = np.zeros_like(r)
    ok = v > 0.0
    out[ok] = r[ok] / np.sqrt(v[ok])
    return out


def _response_at(values: NDArray[np.float64], sigma: float, index: int) -> float | None:
    """Filter response at one array index, or None if the kernel does not fit."""
    k = search_kernel(sigma)
    half = (k.size - 1) // 2
    if index - half < 0 or index + half >= values.size:
        return None
    return float(np.dot(k, values[index - half : index + half + 1]))


def _width_estimate(values: NDArray[np.float64], sigma: float, index: int) -> float | None:
    """Two-scale estimate of the peak standard deviation (channels); None if undefined."""
    s1, s2 = max(1.0, 0.5 * sigma), sigma
    if s2 < 1.3 * s1:
        return None
    r1, r2 = _response_at(values, s1, index), _response_at(values, s2, index)
    if r1 is None or r2 is None or r1 <= 0.0 or r2 <= 0.0:
        return None
    rho = (r1 / r2) ** (2.0 / 3.0) * (s2 / s1) ** 2
    if rho <= 1.0:
        return None
    p2 = (s2 * s2 - rho * s1 * s1) / (rho - 1.0)
    # 1/12: variance added by integrating the peak over one-channel-wide bins
    p2 -= 1.0 / 12.0
    if p2 <= 0.0:
        return None
    return math.sqrt(p2)


@dataclass(slots=True)
class _Raw:
    index: int
    sigma: float
    s: float


def find_peak_candidates(spectrum: Spectrum, settings: PeakSearchSettings) -> PeakSearchResult:
    """Search ``spectrum`` for peak candidates. The spectrum is not modified."""
    n = spectrum.n_channels
    first = settings.first_channel if settings.first_channel is not None else spectrum.first_channel
    last = (
        settings.last_channel
        if settings.last_channel is not None
        else spectrum.first_channel + n - 1
    )
    lo, hi = first - spectrum.first_channel, last - spectrum.first_channel
    if not 0 <= lo < hi < n:
        raise ProcessingError(f"search region {first}..{last} is not inside the spectrum")

    values = spectrum.counts.astype(np.float64)
    variance = np.asarray(spectrum.variance_array(), dtype=np.float64)
    warnings: list[str] = []
    if spectrum.metadata.get("display_only") == "true":
        raise ProcessingError("a display-only (smoothed) spectrum must not be analysed")

    raw: list[_Raw] = []
    responses: dict[float, tuple[NDArray[np.float64], NDArray[np.float64]]] = {}
    for fwhm in sorted(set(settings.fwhm_channels)):
        sigma = fwhm / FWHM_PER_SIGMA
        r, v = filter_response(values, variance, sigma)
        responses[sigma] = (r, v)
        s = np.zeros_like(r)
        ok = v > 0.0
        s[ok] = r[ok] / np.sqrt(v[ok])
        half = math.ceil(KERNEL_HALF_WIDTH_SIGMA * sigma)
        if 2 * half + 1 > n:
            warnings.append(f"scale FWHM {fwhm:g} is too wide for this spectrum and was skipped")
            continue
        distance = max(1, round(settings.min_separation_fwhm * fwhm))
        idx, _ = find_peaks(s[lo : hi + 1], height=settings.threshold, distance=distance)
        raw.extend(_Raw(int(i) + lo, sigma, float(s[int(i) + lo])) for i in idx)

    # merge across scales: the most significant candidate suppresses weaker ones nearby
    raw.sort(key=lambda c: c.s, reverse=True)
    kept: list[_Raw] = []
    for c in raw:
        if all(
            abs(c.index - k.index) >= settings.min_separation_fwhm * k.sigma * FWHM_PER_SIGMA
            for k in kept
        ):
            kept.append(c)
    kept.sort(key=lambda c: c.index)

    verified = spectrum.energy_calibration is not None and spectrum.energy_calibration.is_verified
    built: list[PeakCandidate] = []
    for c in kept:
        r, _ = responses[c.sigma]
        i = c.index
        position = float(i)
        position_unc = math.nan
        if 0 < i < n - 1:
            curvature = r[i + 1] - 2.0 * r[i] + r[i - 1]
            if curvature < 0.0:
                slope = 0.5 * (r[i + 1] - r[i - 1])
                position = i - slope / curvature
                # variance of the slope = variance of y correlated with (k[j-1] - k[j+1]) / 2
                k = search_kernel(c.sigma)
                dk = np.zeros(k.size + 2)
                dk[:-2] += 0.5 * k
                dk[2:] -= 0.5 * k
                half = (dk.size - 1) // 2
                a, b = i - half, i + half + 1
                if a >= 0 and b <= n:
                    var_slope = float(np.dot(dk * dk, variance[a:b]))
                    position_unc = math.sqrt(var_slope) / abs(curvature)
        sigma_p = _width_estimate(values, c.sigma, i)
        p = sigma_p if sigma_p is not None else c.sigma / BEST_SCALE_RATIO
        peak_r = float(r[i])
        area = peak_r * (c.sigma**2 + p**2) ** 1.5 / c.sigma**3
        channel = position + spectrum.first_channel
        energy = None
        if verified:
            assert spectrum.energy_calibration is not None
            energy = float(spectrum.energy_calibration.energy_at(channel))
        built.append(
            PeakCandidate(
                channel=channel,
                channel_uncertainty=position_unc,
                significance=c.s,
                scale_fwhm=c.sigma * FWHM_PER_SIGMA,
                fwhm_estimate=sigma_p * FWHM_PER_SIGMA if sigma_p is not None else None,
                area_estimate=area,
                prominence=area / (p * _SQRT_2PI),
                group=0,
                energy_kev=energy,
            )
        )

    # overlap groups: chain candidates closer than overlap_fwhm x the larger width
    grouped: list[PeakCandidate] = []
    group = -1
    for n_c, cand in enumerate(built):
        if n_c == 0:
            group = 0
        else:
            prev = built[n_c - 1]
            reach = settings.overlap_fwhm * max(prev.fwhm_guess, cand.fwhm_guess)
            if cand.channel - prev.channel >= reach:
                group += 1
        grouped.append(
            PeakCandidate(
                channel=cand.channel,
                channel_uncertainty=cand.channel_uncertainty,
                significance=cand.significance,
                scale_fwhm=cand.scale_fwhm,
                fwhm_estimate=cand.fwhm_estimate,
                area_estimate=cand.area_estimate,
                prominence=cand.prominence,
                group=group,
                energy_kev=cand.energy_kev,
            )
        )

    if spectrum.is_count_data:
        region = spectrum.counts[lo : hi + 1]
        empty = float(np.mean(region == 0))
        if empty > 0.2:
            warnings.append(
                f"{100 * empty:.0f} % of the searched channels are empty: the significance is "
                "not normally distributed there and the threshold is not a Gaussian "
                "false-alarm probability"
            )
    if not verified:
        warnings.append("positions are channel numbers; no verified energy calibration")
    return PeakSearchResult(
        candidates=tuple(grouped),
        settings=settings,
        spectrum_sha256=spectrum.data_sha256,
        first_channel=first,
        last_channel=last,
        warnings=tuple(warnings),
    )


def estimate_fwhm_channels(
    spectrum: Spectrum, trial_fwhm: tuple[float, ...] = (3.0, 5.0, 8.0, 12.0, 20.0, 32.0, 50.0)
) -> float | None:
    """FWHM (channels) of the most significant candidate found at a ladder of trial scales.

    A convenience for choosing detection scales when nothing is known about the
    resolution. Returns None if nothing exceeds a significance of 6.
    """
    usable = tuple(
        w
        for w in trial_fwhm
        if 2 * math.ceil(KERNEL_HALF_WIDTH_SIGMA * w / FWHM_PER_SIGMA) + 1 <= spectrum.n_channels
    )
    if not usable:
        return None
    result = find_peak_candidates(spectrum, PeakSearchSettings(fwhm_channels=usable, threshold=6.0))
    if not result.candidates:
        return None
    best = max(result.candidates, key=lambda c: c.significance)
    return best.fwhm_guess
