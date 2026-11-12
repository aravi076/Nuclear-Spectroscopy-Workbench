"""Automatic peak search when a spectrum is opened, and quality classes of candidates.

A candidate is a feature found by the search — never a transition or a nuclide. The
quality class only says how much the search result can be trusted
(docs/design/UX_REDESIGN_PLAN.md §5):

``strong``              significance ≥ ``STRONG_SIGNIFICANCE`` and a plausible width
``weak``                above the threshold but below ``STRONG_SIGNIFICANCE``
``doubtful``            within 1 σ of the threshold, or width inconsistent with the
                        expected width (ratio outside 0.5–1.6)
``not photopeak-like``  width more than 2.5 × the expected width: a Compton edge,
                        backscatter peak or continuum structure is more likely than a
                        photopeak; not proposed for fitting

The expected width is the resolution model's FWHM at the candidate (when the caller has
one) or, without a model, the median width of the strong candidates of the same search
(stated in the reasons). Excluded candidates are the analyst's decision; they stay
listed but are left out of fitting.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import StrEnum

from nsw.analysis.peaks import PeakCandidate, PeakSearchResult, PeakSearchSettings
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum
from nsw.services.peaks import default_scales, search

DEFAULT_THRESHOLD = 4.0  # significance S, as the Peak Analysis page
STRONG_SIGNIFICANCE = 10.0
WIDTH_RATIO_RANGE = (0.5, 1.6)
NOT_PHOTOPEAK_RATIO = 2.5
CHANNEL_KEY_DECIMALS = 2  # candidates are identified by their channel, rounded


class CandidateQuality(StrEnum):
    STRONG = "strong"
    WEAK = "weak"
    DOUBTFUL = "doubtful"
    NOT_PHOTOPEAK = "not photopeak-like"


@dataclass(frozen=True, slots=True)
class ClassifiedCandidate:
    candidate: PeakCandidate
    quality: CandidateQuality
    reasons: tuple[str, ...]
    excluded: bool = False


def channel_key(channel: float) -> float:
    return round(float(channel), CHANNEL_KEY_DECIMALS)


def auto_search(
    spectrum: Spectrum,
    threshold: float = DEFAULT_THRESHOLD,
    expected_fwhm: Callable[[float], float] | None = None,
    first_channel: int | None = None,
    last_channel: int | None = None,
) -> PeakSearchResult:
    """Search with scales from the resolution model (if given) or the spectrum's own peaks."""
    if not threshold > 0.0:
        raise ProcessingError("the significance threshold must be positive")
    if expected_fwhm is not None:
        mid = spectrum.first_channel + spectrum.n_channels / 2.0
        w = max(2.0, round(float(expected_fwhm(mid)), 1))
        scales: tuple[float, ...] = (w, round(2.0 * w, 1))
    else:
        scales = default_scales(spectrum)
    settings = PeakSearchSettings(
        fwhm_channels=scales,
        threshold=threshold,
        first_channel=first_channel,
        last_channel=last_channel,
    )
    return search(spectrum, settings)


def classify(
    found: PeakSearchResult,
    expected_fwhm: Callable[[float], float] | None = None,
    excluded: Iterable[float] = (),
) -> tuple[ClassifiedCandidate, ...]:
    threshold = found.settings.threshold
    excluded_keys = {channel_key(c) for c in excluded}
    proxy: float | None = None
    if expected_fwhm is None:
        strong = [
            c.fwhm_estimate
            for c in found.candidates
            if c.significance >= STRONG_SIGNIFICANCE and c.fwhm_estimate is not None
        ]
        proxy = statistics.median(strong) if strong else None
    out = []
    for c in found.candidates:
        reasons: list[str] = []
        expected = expected_fwhm(c.channel) if expected_fwhm is not None else proxy
        source = "resolution model" if expected_fwhm is not None else "median of strong peaks"
        ratio = None
        if expected is not None and c.fwhm_estimate is not None and expected > 0.0:
            ratio = c.fwhm_estimate / expected
        if ratio is not None and ratio > NOT_PHOTOPEAK_RATIO:
            quality = CandidateQuality.NOT_PHOTOPEAK
            reasons.append(
                f"width {c.fwhm_estimate:.1f} is {ratio:.1f} × the expected {expected:.1f} "
                f"channels ({source}): Compton edge or continuum structure more likely"
            )
        elif c.significance < threshold + 1.0:
            quality = CandidateQuality.DOUBTFUL
            reasons.append(f"significance {c.significance:.1f} is close to the threshold")
        elif ratio is not None and not WIDTH_RATIO_RANGE[0] <= ratio <= WIDTH_RATIO_RANGE[1]:
            quality = CandidateQuality.DOUBTFUL
            reasons.append(
                f"width {c.fwhm_estimate:.1f} differs from the expected {expected:.1f} "
                f"channels ({source})"
            )
        elif c.significance < STRONG_SIGNIFICANCE:
            quality = CandidateQuality.WEAK
            reasons.append(f"significance {c.significance:.1f}")
        else:
            quality = CandidateQuality.STRONG
            reasons.append(f"significance {c.significance:.1f}")
        if c.fwhm_estimate is None:
            reasons.append("no width estimate")
        out.append(
            ClassifiedCandidate(c, quality, tuple(reasons), channel_key(c.channel) in excluded_keys)
        )
    return tuple(out)


def usable(found: PeakSearchResult, classified: Iterable[ClassifiedCandidate]) -> PeakSearchResult:
    """The search without excluded and not-photopeak-like candidates (for fitting)."""
    keep = {
        channel_key(cc.candidate.channel)
        for cc in classified
        if not cc.excluded and cc.quality is not CandidateQuality.NOT_PHOTOPEAK
    }
    return replace(
        found, candidates=tuple(c for c in found.candidates if channel_key(c.channel) in keep)
    )


def summary(classified: Iterable[ClassifiedCandidate]) -> str:
    items = list(classified)
    if not items:
        return "No peak candidates."
    counts = {q: 0 for q in CandidateQuality}
    excluded = 0
    for cc in items:
        if cc.excluded:
            excluded += 1
        else:
            counts[cc.quality] += 1
    parts = [f"{n} {q.value}" for q, n in counts.items() if n]
    if excluded:
        parts.append(f"{excluded} excluded")
    return f"{len(items)} peak candidates: " + ", ".join(parts) + " (candidates, not nuclides)"
