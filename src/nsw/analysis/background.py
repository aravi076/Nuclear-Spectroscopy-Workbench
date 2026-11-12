"""Background subtraction and background comparison for independent measurements.

Model
-----
Gross spectrum G (live time t_G) and background spectrum B (live time t_B) are
independent Poisson measurements taken with the same detector and gain. With the
live-time scaling factor ``alpha = t_G / t_B``:

    N_net[i]   = G[i] - alpha * B[i]
    var_net[i] = G[i] + alpha**2 * B[i]

* Live times (not real times) are used, so the instrument's dead-time correction is
  applied exactly once. Losses the MCA's live-time clock does not account for (e.g.
  pile-up at high rate) are *not* corrected here.
* The variance uses the observed counts as estimates of the Poisson variances. For
  channels with very few counts this plug-in estimate is poor (it is zero for G = B = 0);
  such channels must be analysed with likelihood methods in the peak-fitting phase.
* Negative net values are kept. They are the unbiased result for channels where the
  background fluctuated high.
* If recorded live times are rounded (``time_quantum_s > 0``) ``alpha`` has a systematic
  uncertainty. It is common to all channels, therefore fully correlated, and is reported
  separately instead of being folded into the per-channel variance.

What this module cannot verify: that the shielding, surroundings and detector gain were
actually the same for both measurements. Metadata checks reject clear mismatches; the
remaining assumption is recorded in the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from nsw.analysis.distributions import chi2_sf
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import (
    MeasurementStatus,
    ProcessingStep,
    Spectrum,
    SpectrumKind,
)
from nsw.core.units import Quantity, Unit

START_TOLERANCE_S = 1.0
HIGH_DEAD_TIME = 0.10
# Hardware parameters (as preserved from Lynx .ava files) that set the channel scale.
GAIN_KEYS = ("[70] Conversion Gain", "[154] Coarse Gain", "[145] Fine Gain", "[158] HV Voltage (V)")
ASSUMPTION = (
    "Assumes identical detector gain, shielding and surroundings for both measurements; "
    "this cannot be verified from the file metadata."
)


@dataclass(frozen=True, slots=True)
class Compatibility:
    """Outcome of the validity checks for combining two spectra."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def same_acquisition(a: Spectrum, b: Spectrum) -> bool:
    """True if two spectra are (saves of) the same acquisition, hence not independent."""
    if a.data_sha256 == b.data_sha256:
        return True
    ta, tb = a.start_time_local, b.start_time_local
    if ta is not None and tb is not None:
        return abs((ta - tb).total_seconds()) <= START_TOLERANCE_S
    return False


def _rounding_std(s: Spectrum) -> float:
    """Standard uncertainty of a time rounded to a step q: q / sqrt(12) (uniform error)."""
    return s.time_quantum_s / math.sqrt(12.0)


def check_compatibility(
    gross: Spectrum, background: Spectrum, *, require_background_kind: bool = True
) -> Compatibility:
    """Decide whether ``background`` may be subtracted from / compared with ``gross``."""
    errors: list[str] = []
    warnings: list[str] = []

    if not gross.is_count_data:
        errors.append(f"gross spectrum is {gross.kind.value} data, not measured counts")
    if not background.is_count_data:
        errors.append(f"background spectrum is {background.kind.value} data, not measured counts")
    if require_background_kind:
        if background.kind is not SpectrumKind.BACKGROUND:
            errors.append("the background spectrum has not been designated as a background")
        if gross.kind is SpectrumKind.BACKGROUND:
            errors.append("the gross spectrum is itself designated as a background")
    if gross.origin is not background.origin:
        errors.append(
            f"cannot combine {gross.origin.value} data with {background.origin.value} data"
        )
    if gross.n_channels != background.n_channels or gross.first_channel != background.first_channel:
        errors.append(
            f"channel ranges differ ({gross.n_channels} vs {background.n_channels} channels)"
        )
    for name, s in (("gross", gross), ("background", background)):
        if s.live_time_s is None:
            errors.append(f"{name} spectrum has no live time")
    if same_acquisition(gross, background):
        errors.append(
            "both spectra belong to the same acquisition (identical data or start time); "
            "they are not independent measurements"
        )
    if background.measurement_status is MeasurementStatus.INTERMEDIATE_SAVE:
        errors.append(
            "the background is an intermediate save of a longer acquisition; "
            "use the final save of that acquisition"
        )
    if gross.detector_id and background.detector_id and gross.detector_id != background.detector_id:
        errors.append(f"different detectors: {gross.detector_id!r} and {background.detector_id!r}")
    elif not (gross.detector_id and background.detector_id):
        warnings.append("detector identity is not recorded in both files; same detector is assumed")

    shared = [k for k in GAIN_KEYS if k in gross.metadata and k in background.metadata]
    differing = [k for k in shared if gross.metadata[k] != background.metadata[k]]
    if differing:
        errors.append(
            "gain-determining hardware settings differ: "
            + ", ".join(f"{k} ({gross.metadata[k]} vs {background.metadata[k]})" for k in differing)
        )
    elif not shared:
        warnings.append(
            "hardware gain settings are not recorded in both files; equal gain is assumed"
        )
    ge, be = gross.embedded_calibration, background.embedded_calibration
    if ge is not None and be is not None and ge.coefficients != be.coefficients:
        warnings.append(
            "the vendor calibrations stored in the two files differ, which suggests the "
            "MCA settings were changed between the measurements"
        )

    for name, s in (("gross", gross), ("background", background)):
        if s.time_quantum_s > 0:
            warnings.append(
                f"{name} live time is rounded to {s.time_quantum_s:g} s; the scaling factor "
                "carries a correlated systematic uncertainty (reported separately)"
            )
        dead = s.dead_time_fraction
        if dead is not None and dead > HIGH_DEAD_TIME and s.time_quantum_s == 0:
            warnings.append(
                f"{name} dead time is {100 * dead:.1f} %; pile-up losses are not corrected "
                "by live-time scaling"
            )
    return Compatibility(tuple(errors), tuple(warnings))


@dataclass(frozen=True)
class BackgroundSubtraction:
    """Result of a background subtraction, with everything needed to audit it."""

    gross: Spectrum
    background: Spectrum
    net: Spectrum
    alpha: float
    alpha_sys: float  # standard uncertainty of alpha from live-time rounding (correlated)
    compatibility: Compatibility

    def region(self, first_channel: int, last_channel: int) -> Quantity:
        """Net counts summed over channels ``first_channel..last_channel`` (inclusive).

        ``stat`` is the Poisson uncertainty sqrt(sum G + alpha^2 sum B); ``sys`` is the
        contribution of the scaling-factor uncertainty, alpha_sys * sum B.
        """
        lo = first_channel - self.net.first_channel
        hi = last_channel - self.net.first_channel + 1
        if not 0 <= lo < hi <= self.net.n_channels:
            raise ProcessingError(
                f"channel region {first_channel}..{last_channel} is outside the spectrum"
            )
        net_sum = float(self.net.counts[lo:hi].sum())
        var = float(self.net.variance_array()[lo:hi].sum())
        b_sum = float(self.background.counts[lo:hi].sum())
        return Quantity(net_sum, Unit.COUNT, stat=math.sqrt(var), sys=self.alpha_sys * b_sum)


def subtract_background(gross: Spectrum, background: Spectrum) -> BackgroundSubtraction:
    """Net spectrum G - alpha*B with per-channel variance G + alpha^2*B.

    Raises :class:`ProcessingError` listing every reason if the combination is invalid.
    The inputs are not modified.
    """
    compat = check_compatibility(gross, background)
    if not compat.ok:
        raise ProcessingError("background subtraction refused: " + "; ".join(compat.errors))
    assert gross.live_time_s is not None and background.live_time_s is not None  # noqa: S101 - type narrowing: check_compatibility above requires both live times
    alpha = gross.live_time_s / background.live_time_s
    rel = math.hypot(
        _rounding_std(gross) / gross.live_time_s, _rounding_std(background) / background.live_time_s
    )
    alpha_sys = alpha * rel

    g = gross.counts.astype(np.float64)
    b = background.counts.astype(np.float64)
    net_values = g - alpha * b
    net_variance = g + alpha * alpha * b

    step = ProcessingStep(
        "background-subtraction",
        {
            "model": "N_net = G - alpha*B; var = G + alpha^2*B (independent Poisson)",
            "alpha": alpha,
            "alpha_definition": "gross live time / background live time",
            "alpha_sys_uncertainty": alpha_sys,
            "gross_live_time_s": gross.live_time_s,
            "background_live_time_s": background.live_time_s,
            "gross": _label(gross),
            "background": _label(background),
            "gross_total_counts": int(gross.counts.sum()),
            "background_total_counts": int(background.counts.sum()),
            "assumption": ASSUMPTION,
        },
        parents=(gross.data_sha256, background.data_sha256),
    )
    net = Spectrum(
        counts=net_values,
        variance=net_variance,
        kind=SpectrumKind.NET,
        origin=gross.origin,
        live_time_s=gross.live_time_s,
        real_time_s=gross.real_time_s,
        start_time=gross.start_time,
        detector_id=gross.detector_id or background.detector_id,
        sample_name=gross.sample_name,
        sample_description=gross.sample_description,
        first_channel=gross.first_channel,
        embedded_calibration=gross.embedded_calibration,
        energy_calibration=gross.energy_calibration,
        source=gross.source,
        synthetic_id=gross.synthetic_id,
        time_quantum_s=gross.time_quantum_s,
        measurement_status=MeasurementStatus.DERIVED,
        metadata=dict(gross.metadata),
        provenance=(*gross.provenance, step),
        warnings=(*gross.warnings, *compat.warnings, ASSUMPTION),
    )
    return BackgroundSubtraction(gross, background, net, alpha, alpha_sys, compat)


def _label(s: Spectrum) -> str:
    return s.source.filename if s.source else (s.synthetic_id or "in-memory spectrum")


# ---------------------------------------------------------------------- comparison
@dataclass(frozen=True, slots=True)
class RateComparison:
    """Consistency test of the count rates of two independent spectra."""

    rate_a: Quantity  # total count rate, count/s
    rate_b: Quantity
    chi2: float
    ndf: int
    p_value: float
    block_width: int
    blocks_skipped: int  # blocks with too few counts for the test

    @property
    def reduced_chi2(self) -> float:
        return self.chi2 / self.ndf if self.ndf else math.nan


def _total_rate(s: Spectrum) -> Quantity:
    assert s.live_time_s is not None  # noqa: S101 - type narrowing: only called after check_compatibility
    n = float(s.counts.sum())
    t = s.live_time_s
    return Quantity(
        n / t,
        Unit.COUNT_PER_SECOND,
        stat=math.sqrt(n) / t,
        sys=(n / t) * _rounding_std(s) / t,
    )


def block_sums(counts: NDArray[np.int64], width: int) -> NDArray[np.int64]:
    """Sum consecutive groups of ``width`` channels (a trailing partial group is dropped)."""
    if width < 1:
        raise ProcessingError("block width must be >= 1")
    n = (counts.size // width) * width
    if n == 0:
        raise ProcessingError("block width exceeds the number of channels")
    return np.asarray(counts[:n].reshape(-1, width).sum(axis=1), dtype=np.int64)


def compare_count_rates(
    a: Spectrum, b: Spectrum, *, block_width: int = 64, min_block_counts: int = 20
) -> RateComparison:
    """Test whether two independent spectra have the same count-rate spectrum.

    Channels are summed in blocks of ``block_width``. Under the hypothesis of equal
    rates, and given the block total n_k = A_k + B_k, the count A_k is binomial with
    p = t_A / (t_A + t_B). The statistic

        chi2 = sum_k (A_k - p n_k)^2 / (n_k p (1 - p))

    then follows a chi-square distribution with one degree of freedom per block (no
    parameter is fitted: p is fixed by the live times). Blocks with fewer than
    ``min_block_counts`` total counts are left out, because the chi-square
    approximation of the binomial is unreliable there.
    """
    compat = check_compatibility(a, b, require_background_kind=False)
    if not compat.ok:
        raise ProcessingError("comparison refused: " + "; ".join(compat.errors))
    assert a.live_time_s is not None and b.live_time_s is not None  # noqa: S101 - type narrowing: check_compatibility above requires both live times
    p = a.live_time_s / (a.live_time_s + b.live_time_s)
    ak = block_sums(a.counts, block_width).astype(np.float64)
    bk = block_sums(b.counts, block_width).astype(np.float64)
    nk = ak + bk
    use = nk >= min_block_counts
    ndf = int(use.sum())
    if ndf == 0:
        raise ProcessingError("too few counts for a rate comparison at this block width")
    chi2 = float(np.sum((ak[use] - p * nk[use]) ** 2 / (nk[use] * p * (1.0 - p))))
    return RateComparison(
        rate_a=_total_rate(a),
        rate_b=_total_rate(b),
        chi2=chi2,
        ndf=ndf,
        p_value=float(chi2_sf(chi2, ndf)),
        block_width=block_width,
        blocks_skipped=int((~use).sum()),
    )


# ---------------------------------------------------------------------- display smoothing
def smooth_for_display(spectrum: Spectrum, window: int = 5) -> Spectrum:
    """Moving-average copy **for viewing only**.

    Smoothing correlates neighbouring channels and distorts peak shapes; the result is
    marked ``display_only`` and must never be used as input to an analysis. The stored
    variance is that of the average assuming independent channels (var / window per
    contributing channel) and ignores the induced correlations.
    """
    if window < 3 or window % 2 == 0:
        raise ProcessingError("smoothing window must be an odd number >= 3")
    if window > spectrum.n_channels:
        raise ProcessingError("smoothing window exceeds the number of channels")
    kernel = np.full(window, 1.0 / window)
    values = np.convolve(spectrum.counts.astype(np.float64), kernel, mode="same")
    variance = np.convolve(spectrum.variance_array(), kernel**2, mode="same")
    step = ProcessingStep(
        "display-smoothing",
        {"method": "moving average", "window_channels": window, "display_only": True},
        parents=(spectrum.data_sha256,),
        note="visual aid only; not statistically valid input for analysis",
    )
    return Spectrum(
        counts=values,
        variance=variance,
        kind=SpectrumKind.PROCESSED,
        origin=spectrum.origin,
        live_time_s=spectrum.live_time_s,
        real_time_s=spectrum.real_time_s,
        start_time=spectrum.start_time,
        detector_id=spectrum.detector_id,
        sample_name=spectrum.sample_name,
        first_channel=spectrum.first_channel,
        embedded_calibration=spectrum.embedded_calibration,
        energy_calibration=spectrum.energy_calibration,
        source=spectrum.source,
        synthetic_id=spectrum.synthetic_id,
        time_quantum_s=spectrum.time_quantum_s,
        measurement_status=MeasurementStatus.DERIVED,
        metadata={**spectrum.metadata, "display_only": "true"},
        provenance=(*spectrum.provenance, step),
        warnings=(*spectrum.warnings, "DISPLAY ONLY - smoothed data, not for analysis"),
    )
