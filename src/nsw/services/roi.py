"""Regions of interest (ROI): the analyst's selections on a spectrum, Qt-free.

An ROI is a channel range plus intent. It never changes the spectrum. Everything the
interface does with an ROI goes through these functions (docs/design/UX_REDESIGN_PLAN.md §4):

- ``make_roi`` / ``validate_roi``: a range inside the spectrum, at least
  ``MIN_CHANNELS`` channels.
- ``candidates_in``: peak candidates of a search that lie inside an ROI.
- ``propose_rois``: one ROI per overlap group of a peak search, with ``margin_fwhm``
  FWHM of continuum on each side (default 3); pairs closer than ``close_pair_fwhm``
  (default 1.5) are reported as possibly unresolved. Owner-approved defaults.
- ``net_area``: classical ROI net area — gross counts of the inner channels minus a
  straight continuum through the mean of ``edge_channels`` channels at each end; the
  continuum estimate carries its own variance. Works for raw and derived spectra (the
  variance comes from the spectrum).
- ``limits``: decision threshold and detection limit of that net area with the method
  the analyst selects (ISO 11929 framework or Currie), via ``nsw.analysis.limits``.
- ``fit_roi``: the existing peak fit on the ROI's range and peaks.
- ``record`` / ``current_rois``: ROI changes are analyst decisions, appended to the
  project's decision log (schema 2) and replayed when the project is reopened.
"""

from __future__ import annotations

import itertools
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum

import numpy as np

from nsw.analysis.distributions import chi2_sf
from nsw.analysis.fitting import (
    BackgroundShape,
    FitMethod,
    FitResult,
    FitSettings,
    fit_peaks,
)
from nsw.analysis.limits import (
    CharacteristicLimits,
    CountingModel,
    LimitMethod,
    LimitSettings,
    gaussian_bins,
    limits_from_u_tilde,
    poisson_u_tilde,
)
from nsw.analysis.limits import characteristic_limits as _limits
from nsw.analysis.peaks import (
    PeakCandidate,
    PeakSearchResult,
    PeakSearchSettings,
    estimate_fwhm_channels,
    find_peak_candidates,
)
from nsw.analysis.resolution import ResolutionFit, ResolutionVariable
from nsw.analysis.tailfit import TailTest, tail_test
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum
from nsw.database.store import ProjectStore

MIN_CHANNELS = 3
DEFAULT_MARGIN_FWHM = 3.0
DEFAULT_CLOSE_PAIR_FWHM = 1.5
DEFAULT_EDGE_CHANNELS = 3


class RoiKind(StrEnum):
    SINGLE = "single"  # one peak
    MULTIPLET = "multiplet"  # several peaks fitted together
    CUSTOM = "custom"  # any range the analyst wants to examine


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True, slots=True)
class Roi:
    data_sha256: str
    first_channel: int
    last_channel: int
    kind: RoiKind = RoiKind.CUSTOM
    peak_channels: tuple[float, ...] = ()
    fwhm_channels: tuple[float, ...] = ()  # starting widths, one per peak
    label: str = ""
    roi_id: str = field(default_factory=_new_id)

    @property
    def n_channels(self) -> int:
        return self.last_channel - self.first_channel + 1

    def contains(self, channel: float) -> bool:
        return self.first_channel <= channel <= self.last_channel

    def moved_to(self, first_channel: int, last_channel: int) -> Roi:
        """The same ROI (same id) with new edges; peaks outside the new range are dropped."""
        keep = [
            (c, w)
            for c, w in zip(self.peak_channels, self.fwhm_channels, strict=True)
            if first_channel <= c <= last_channel
        ]
        return Roi(
            self.data_sha256,
            first_channel,
            last_channel,
            self.kind,
            tuple(c for c, _ in keep),
            tuple(w for _, w in keep),
            self.label,
            self.roi_id,
        )

    def as_record(self) -> dict[str, object]:
        """JSON-ready form for the project's decision log."""
        return {
            "roi_id": self.roi_id,
            "first_channel": self.first_channel,
            "last_channel": self.last_channel,
            "kind": self.kind.value,
            "peak_channels": list(self.peak_channels),
            "fwhm_channels": list(self.fwhm_channels),
            "label": self.label,
        }


def validate_roi(roi: Roi, spectrum: Spectrum) -> list[str]:
    """Reasons why ``roi`` cannot be used on ``spectrum`` (empty list: usable)."""
    problems = []
    if roi.data_sha256 != spectrum.data_sha256:
        problems.append("the region belongs to a different spectrum")
    last_possible = spectrum.first_channel + spectrum.n_channels - 1
    if not spectrum.first_channel <= roi.first_channel <= roi.last_channel <= last_possible:
        problems.append(
            f"channels {roi.first_channel}..{roi.last_channel} are not inside the spectrum "
            f"({spectrum.first_channel}..{last_possible})"
        )
    if roi.n_channels < MIN_CHANNELS:
        problems.append(f"a region needs at least {MIN_CHANNELS} channels")
    if len(roi.peak_channels) != len(roi.fwhm_channels):
        problems.append("one starting width per peak is required")
    if any(not roi.contains(c) for c in roi.peak_channels):
        problems.append("a peak position lies outside the region")
    if any(not (w > 0.0 and math.isfinite(w)) for w in roi.fwhm_channels):
        problems.append("starting widths must be positive")
    return problems


def make_roi(
    spectrum: Spectrum,
    first_channel: int,
    last_channel: int,
    kind: RoiKind = RoiKind.CUSTOM,
    peak_channels: tuple[float, ...] = (),
    fwhm_channels: tuple[float, ...] = (),
    label: str = "",
) -> Roi:
    """A validated ROI; edges may be given in either order."""
    lo, hi = sorted((int(first_channel), int(last_channel)))
    roi = Roi(spectrum.data_sha256, lo, hi, RoiKind(kind), peak_channels, fwhm_channels, label)
    problems = validate_roi(roi, spectrum)
    if problems:
        raise ProcessingError("region of interest refused: " + "; ".join(problems))
    return roi


def candidates_in(roi: Roi, found: PeakSearchResult) -> tuple[PeakCandidate, ...]:
    if found.spectrum_sha256 != roi.data_sha256:
        raise ProcessingError("the peak search belongs to a different spectrum")
    return tuple(c for c in found.candidates if roi.contains(c.channel))


@dataclass(frozen=True, slots=True)
class RoiProposal:
    roi: Roi
    reasons: tuple[str, ...]  # why the candidates were grouped, and any warnings


def propose_rois(
    spectrum: Spectrum,
    found: PeakSearchResult,
    margin_fwhm: float = DEFAULT_MARGIN_FWHM,
    close_pair_fwhm: float = DEFAULT_CLOSE_PAIR_FWHM,
    expected_fwhm: Callable[[float], float] | None = None,
) -> tuple[RoiProposal, ...]:
    """One ROI per overlap group of the search (candidates whose fit regions overlap).

    Widths for the margin, the close-pair test and the fit start come from
    ``expected_fwhm(channel)`` when given (a resolution model, see
    :func:`fwhm_from_resolution`), else from the search's estimates. The search
    underestimates the width of blended peaks, so the model is preferred.
    """
    if found.spectrum_sha256 != spectrum.data_sha256:
        raise ProcessingError("the peak search belongs to a different spectrum")
    if not (margin_fwhm > 0.0 and close_pair_fwhm > 0.0):
        raise ProcessingError("margin and close-pair distance must be positive")
    first_possible = spectrum.first_channel
    last_possible = spectrum.first_channel + spectrum.n_channels - 1
    out = []
    for group in found.groups():
        channels = tuple(c.channel for c in group)
        if expected_fwhm is not None:
            widths = tuple(float(expected_fwhm(c)) for c in channels)
            width_source = "resolution model"
        else:
            widths = tuple(c.fwhm_guess for c in group)
            width_source = "peak-search estimate"
        margin = margin_fwhm * max(widths)
        lo = max(first_possible, math.floor(min(channels) - margin))
        hi = min(last_possible, math.ceil(max(channels) + margin))
        reasons = []
        if len(group) == 1:
            kind = RoiKind.SINGLE
        else:
            kind = RoiKind.MULTIPLET
            reasons.append(
                f"{len(group)} candidates within the search's overlap distance "
                f"({found.settings.overlap_fwhm:g} FWHM) are fitted together"
            )
            ordered = sorted(zip(channels, widths, strict=True))
            for (ca, wa), (cb, wb) in itertools.pairwise(ordered):
                gap = (cb - ca) / max(wa, wb)
                if gap < close_pair_fwhm:
                    reasons.append(
                        f"channels {ca:.1f} and {cb:.1f} are {gap:.2f} FWHM apart "
                        f"(width from the {width_source}): possibly unresolved, check the "
                        "multiplet evidence"
                    )
        if lo == first_possible or hi == last_possible:
            reasons.append("the region reaches the end of the spectrum: continuum margin shortened")
        roi = Roi(spectrum.data_sha256, lo, hi, kind, channels, widths)
        out.append(RoiProposal(roi, tuple(reasons)))
    return tuple(out)


def fwhm_from_resolution(fit: ResolutionFit) -> Callable[[float], float]:
    """Expected FWHM (channels) at a channel from a resolution model in channel units.

    Models in keV need a verified energy calibration to be used on channels; they are
    refused here rather than converted silently.
    """
    if fit.variable is not ResolutionVariable.CHANNEL:
        raise ProcessingError(
            "the resolution model is in keV; use a channel-based model for region proposals"
        )

    def expected(channel: float) -> float:
        width, _, _ = fit.fwhm(channel)
        return float(width[0])

    return expected


@dataclass(frozen=True, slots=True)
class NetArea:
    gross: float
    background: float  # continuum estimate under the inner channels
    background_variance: float
    net: float
    net_uncertainty: float
    inner_first: int
    inner_last: int
    edge_channels: int

    @property
    def counting_model(self) -> CountingModel:
        return CountingModel(max(self.background, 0.0), self.background_variance)


def net_area(roi: Roi, spectrum: Spectrum, edge_channels: int = DEFAULT_EDGE_CHANNELS) -> NetArea:
    """Gross minus a straight continuum through the mean of the edge channels.

    The ``edge_channels`` first and last channels of the ROI estimate the continuum;
    the remaining inner channels are summed. Negative net values are kept.
    """
    problems = validate_roi(roi, spectrum)
    if problems:
        raise ProcessingError("; ".join(problems))
    if edge_channels < 1 or roi.n_channels < 2 * edge_channels + 1:
        raise ProcessingError(
            f"the region needs at least {2 * edge_channels + 1} channels for "
            f"{edge_channels} continuum channels at each end"
        )
    lo = roi.first_channel - spectrum.first_channel
    hi = roi.last_channel - spectrum.first_channel + 1
    y = np.asarray(spectrum.counts, dtype=np.float64)[lo:hi]
    v = np.asarray(spectrum.variance_array(), dtype=np.float64)[lo:hi]
    e = edge_channels
    left, right = float(y[:e].sum()), float(y[-e:].sum())
    var_left, var_right = float(v[:e].sum()), float(v[-e:].sum())
    inner = y[e:-e]
    n_inner = inner.size
    gross = float(inner.sum())
    background = n_inner * (left / e + right / e) / 2.0
    background_variance = (n_inner / (2.0 * e)) ** 2 * (var_left + var_right)
    gross_variance = float(v[e:-e].sum())
    net = gross - background
    return NetArea(
        gross,
        background,
        background_variance,
        net,
        math.sqrt(gross_variance + background_variance),
        roi.first_channel + e,
        roi.last_channel - e,
        e,
    )


def limits(
    area: NetArea,
    settings: LimitSettings,
    w: float = 1.0,
    w_rel_uncertainty: float = 0.0,
) -> CharacteristicLimits:
    """Decision threshold and detection limit of the net area (method from ``settings``)."""
    model = CountingModel(max(area.background, 0.0), area.background_variance, w, w_rel_uncertainty)
    return _limits(model, settings)


def fit_roi(spectrum: Spectrum, roi: Roi, settings: FitSettings) -> FitResult:
    problems = validate_roi(roi, spectrum)
    if problems:
        raise ProcessingError("; ".join(problems))
    if not roi.peak_channels:
        raise ProcessingError("the region has no peak to fit: add or select a candidate")
    return fit_peaks(
        spectrum,
        roi.first_channel,
        roi.last_channel,
        roi.peak_channels,
        roi.fwhm_channels,
        settings,
    )


ROI_ACTIONS = ("roi_create", "roi_edit", "roi_delete")


def record(store: ProjectStore, roi: Roi, action: str, note: str = "") -> int:
    """Append an ROI change to the project's decision log (the spectrum must be stored)."""
    if action not in ROI_ACTIONS:
        raise ProcessingError(f"unknown ROI action {action!r}; expected one of {ROI_ACTIONS}")
    return store.add_decision(
        f"roi:{roi.roi_id}", action, data_sha256=roi.data_sha256, value=roi.as_record(), note=note
    )


def current_rois(store: ProjectStore, data_sha256: str) -> tuple[Roi, ...]:
    """The ROIs of a spectrum after replaying every recorded change, in creation order."""
    state: dict[str, Roi] = {}
    for d in store.decisions(data_sha256, subject_prefix="roi:"):
        roi_id = d.subject.removeprefix("roi:")
        if d.action == "roi_delete":
            state.pop(roi_id, None)
            continue
        v = d.value
        state[roi_id] = Roi(
            data_sha256,
            int(v["first_channel"]),
            int(v["last_channel"]),
            RoiKind(v["kind"]),
            tuple(float(c) for c in v["peak_channels"]),
            tuple(float(w) for w in v["fwhm_channels"]),
            str(v.get("label", "")),
            roi_id,
        )
    return tuple(state.values())


# ---------------------------------------------------------------------- table for the UI
LIMIT_METHOD_LABELS = {
    LimitMethod.ISO_11929.value: "ISO 11929 framework (conformity not verified)",
    LimitMethod.CURRIE.value: "Currie (1968)",
}
ROI_HEADERS = (
    "Region",
    "Channels",
    "Net counts",
    "Decision threshold",
    "Detection limit",
    "Decision",
    "Note",
)


def limit_settings(
    method: str = LimitMethod.ISO_11929.value,
    alpha: float = 0.05,
    beta: float = 0.05,
    gamma: float = 0.05,
) -> LimitSettings:
    """Limit settings from plain values (as kept by the interface or the settings file)."""
    return LimitSettings(LimitMethod(method), alpha, beta, gamma)


def roi_rows(
    spectrum: Spectrum,
    rois: list[Roi],
    settings: LimitSettings,
    edge_channels: int = DEFAULT_EDGE_CHANNELS,
) -> list[list[str]]:
    """One row per region: net counts with uncertainty and the characteristic limits.

    Counts only (w = 1): no efficiency or activity is implied, so the rows are allowed
    for experimental spectra in channel units.
    """
    rows = []
    for n, roi in enumerate(rois, start=1):
        name = roi.label or f"R{n}"
        channels = f"{roi.first_channel}–{roi.last_channel} ({roi.n_channels})"
        try:
            area = net_area(roi, spectrum, edge_channels)
            lim = limits(area, settings)
        except ProcessingError as exc:
            rows.append([name, channels, "—", "—", "—", "—", str(exc)])
            continue
        if lim.detection_limit is None:
            detection = "does not exist"
        else:
            detection = f"{lim.detection_limit:,.1f}"
        decision = (
            "above the decision threshold"
            if area.net > lim.decision_threshold
            else "not above the decision threshold"
        )
        note = (
            f"continuum from {edge_channels} channels at each end; "
            f"{LIMIT_METHOD_LABELS[lim.method.value]}, "
            f"α = {settings.alpha:g}, β = {settings.beta:g}"
        )
        rows.append(
            [
                name,
                channels,
                f"{area.net:,.1f} ± {area.net_uncertainty:,.1f}",
                f"{lim.decision_threshold:,.1f}",
                detection,
                decision,
                note,
            ]
        )
    return rows


# ---------------------------------------------------------------------- peaks and models
FALLBACK_FWHM_CHANNELS = 4.0  # only when nothing in the spectrum allows an estimate
COMPARISON_P = 0.01  # a richer continuum model must improve the fit at this level


def _width_guess(spectrum: Spectrum) -> float:
    estimate = estimate_fwhm_channels(spectrum)
    return float(estimate) if estimate is not None else FALLBACK_FWHM_CHANNELS


def with_peaks(roi: Roi, channels: tuple[float, ...], widths: tuple[float, ...]) -> Roi:
    """The same region (same id) with the given peaks; the kind follows the peak count."""
    kind = roi.kind
    if kind is not RoiKind.CUSTOM:
        kind = RoiKind.SINGLE if len(channels) == 1 else RoiKind.MULTIPLET
    order = sorted(zip(channels, widths, strict=True))
    return Roi(
        roi.data_sha256,
        roi.first_channel,
        roi.last_channel,
        kind,
        tuple(c for c, _ in order),
        tuple(w for _, w in order),
        roi.label,
        roi.roi_id,
    )


def add_peak(roi: Roi, spectrum: Spectrum, channel: float, fwhm: float | None = None) -> Roi:
    """Add an analyst-chosen peak position (width: given, from the neighbours, or estimated)."""
    if not roi.contains(channel):
        raise ProcessingError(f"channel {channel:g} is outside the region")
    if any(abs(channel - c) < 0.5 for c in roi.peak_channels):
        raise ProcessingError(f"the region already has a peak at channel {channel:g}")
    if fwhm is None:
        fwhm = float(np.median(roi.fwhm_channels)) if roi.fwhm_channels else _width_guess(spectrum)
    return with_peaks(roi, (*roi.peak_channels, float(channel)), (*roi.fwhm_channels, float(fwhm)))


def peaks_for_roi(
    spectrum: Spectrum, roi: Roi, found: PeakSearchResult | None = None
) -> tuple[Roi, str]:
    """The region with peak positions to fit, and where they came from.

    Keeps the region's own peaks; otherwise takes the candidates of an existing search of
    this spectrum inside the region; otherwise searches the region itself.
    """
    if roi.peak_channels:
        return roi, "peaks of the region"
    if found is not None and found.spectrum_sha256 == spectrum.data_sha256:
        inside = candidates_in(roi, found)
        if inside:
            channels = tuple(c.channel for c in inside)
            widths = tuple(c.fwhm_guess for c in inside)
            return with_peaks(roi, channels, widths), "candidates of the peak search"
    w = _width_guess(spectrum)
    settings = PeakSearchSettings(
        fwhm_channels=(max(2.0, round(w, 1)), max(4.0, round(2 * w, 1))),
        first_channel=roi.first_channel,
        last_channel=roi.last_channel,
    )
    try:
        local = find_peak_candidates(spectrum, settings)
    except ProcessingError as exc:
        raise ProcessingError(f"no peak to fit in the region ({exc})") from exc
    if not local.candidates:
        raise ProcessingError(
            "no peak candidate found in the region: add one with the context menu"
        )
    channels = tuple(c.channel for c in local.candidates)
    widths = tuple(c.fwhm_guess for c in local.candidates)
    return with_peaks(roi, channels, widths), "candidates found by searching the region"


@dataclass(frozen=True, slots=True)
class ModelFit:
    name: str
    settings: FitSettings
    result: FitResult | None
    error: str


@dataclass(frozen=True, slots=True)
class ModelComparison:
    fits: tuple[ModelFit, ...]
    preferred: str
    reason: str


MODEL_VARIANTS = (
    ("constant continuum", BackgroundShape.CONSTANT, False),
    ("linear continuum", BackgroundShape.LINEAR, False),
    ("linear continuum + step", BackgroundShape.LINEAR, True),
)


def compare_models(spectrum: Spectrum, roi: Roi, base: FitSettings) -> ModelComparison:
    """Fit the region with nested continuum models and keep the simplest adequate one.

    The models are nested (constant, linear, linear + step). Starting from the simplest
    converged fit, a richer model is preferred only if it lowers the fit statistic
    significantly: p < 0.01 for the change of the statistic against a chi-square with the
    change in degrees of freedom. The step fraction is bounded at 0, so for that
    comparison the p-value is halved (mixture of chi-square with 0 and 1 degree). All fits
    use the same estimator and range, so their statistics are comparable.
    """
    if not roi.peak_channels:
        raise ProcessingError("the region has no peak to fit")
    fits = []
    for name, shape, step in MODEL_VARIANTS:
        settings = replace(base, background=shape, step=step)
        try:
            fits.append(ModelFit(name, settings, fit_roi(spectrum, roi, settings), ""))
        except ProcessingError as exc:
            fits.append(ModelFit(name, settings, None, str(exc)))
    usable = [f for f in fits if f.result is not None and f.result.converged]
    if not usable:
        return ModelComparison(tuple(fits), "", "no model converged")
    chosen = usable[0]
    reason = f"{chosen.name}: simplest converged model"
    for richer in usable[1:]:
        a, b = chosen.result, richer.result
        if a is None or b is None:
            continue
        dk = a.ndf - b.ndf
        if dk <= 0:
            continue
        delta = a.statistic - b.statistic
        p = chi2_sf(max(delta, 0.0), dk)
        if richer.settings.step and not chosen.settings.step:
            p *= 0.5  # boundary: step fraction >= 0
        if p < COMPARISON_P:
            chosen = richer
            reason = (
                f"{richer.name}: improves the fit significantly "
                f"(change of {b.statistic_name} {delta:.1f} for {dk} parameter(s), p = {p:.2g})"
            )
        else:
            reason += (
                f"; {richer.name} not preferred "
                f"(change of {b.statistic_name} {delta:.1f}, p = {p:.2g})"
            )
    return ModelComparison(tuple(fits), chosen.name, reason)


COMPARISON_HEADERS = ("Model", "Statistic", "ndf", "p-value", "Areas", "Status")


def comparison_rows(cmp: ModelComparison) -> list[list[str]]:
    rows = []
    for f in cmp.fits:
        r = f.result
        if r is None:
            rows.append([f.name, "—", "—", "—", "—", f.error])
            continue
        areas = "; ".join(f"{p.area:,.0f} ± {p.area_unc:,.0f}" for p in r.peaks)
        if not r.converged:
            status = "not converged"
        else:
            status = "preferred" if f.name == cmp.preferred else ""
        p_text = "—" if math.isnan(r.p_value) else f"{r.p_value:.3g}"
        rows.append(
            [f.name, f"{r.statistic_name} {r.statistic:,.1f}", str(r.ndf), p_text, areas, status]
        )
    return rows


# ---------------------------------------------------------------------- limits from a fit
def fit_limits(result: FitResult, peak_index: int, settings: LimitSettings) -> CharacteristicLimits:
    """Decision threshold and detection limit of one fitted peak area (counts).

    ũ(Ñ) comes from the fit model itself (``analysis.limits.poisson_u_tilde``): the fitted
    continuum, the peak's fitted position and width held fixed, and as nuisance
    parameters the continuum parameters and the areas of the other peaks of the region.
    Only for Poisson fits of count data; the step fraction is held at its fitted value.
    """
    if result.method != "poisson":
        raise ProcessingError("limits from a fit need a Poisson fit of count data")
    if not 0 <= peak_index < len(result.peaks):
        raise ProcessingError("no such peak in the fit")
    x = np.asarray(result.channels, dtype=np.float64)
    peak = result.peaks[peak_index]
    shape = gaussian_bins(x, peak.centroid, peak.sigma)
    nuisance = [
        gaussian_bins(x, other.centroid, other.sigma)
        for i, other in enumerate(result.peaks)
        if i != peak_index
    ]
    background = result.settings.background
    if background is BackgroundShape.CONSTANT:
        nuisance.append(np.ones_like(x))
    elif background is BackgroundShape.LINEAR:
        t = (x - x[0]) / max(x[-1] - x[0], 1.0)
        nuisance += [1.0 - t, t]
    u_tilde = poisson_u_tilde(
        np.asarray(result.fitted_continuum, dtype=np.float64),
        shape,
        np.vstack(nuisance) if nuisance else None,
    )
    return limits_from_u_tilde(u_tilde, settings)


def fit_limit_text(result: FitResult, settings: LimitSettings) -> str:
    parts = []
    for i, peak in enumerate(result.peaks):
        try:
            lim = fit_limits(result, i, settings)
        except ProcessingError as exc:
            return f"Limits from the fit: {exc}"
        detection = (
            "does not exist" if lim.detection_limit is None else f"{lim.detection_limit:,.1f}"
        )
        decision = "above" if peak.area > lim.decision_threshold else "not above"
        parts.append(
            f"peak at channel {peak.centroid:.1f}: area {peak.area:,.1f} ± {peak.area_unc:,.1f} "
            f"is {decision} the decision threshold {lim.decision_threshold:,.1f}; "
            f"detection limit {detection} counts"
        )
    label = LIMIT_METHOD_LABELS[settings.method.value]
    return f"Limits from the fit ({label}): " + "; ".join(parts) + "."


# ---------------------------------------------------------------------- low-energy tail
def run_tail_test(spectrum: Spectrum, roi: Roi, replicas: int = 100) -> TailTest:
    """Gaussian vs Gaussian + low-side tail for a region (docs/design/HPGE_PEAK_TAIL_DESIGN.md).

    Poisson fits with a linear continuum; the tail is preferred only if the deviance drop
    exceeds the 99 % bootstrap quantile under the Gaussian model. Never applied silently.
    """
    if not roi.peak_channels:
        raise ProcessingError("the region has no peak to fit")
    settings = FitSettings(method=FitMethod.POISSON, background=BackgroundShape.LINEAR)
    gauss = fit_roi(spectrum, roi, settings)
    fwhm = float(np.mean([p.fwhm for p in gauss.peaks]))
    return tail_test(
        spectrum,
        roi.first_channel,
        roi.last_channel,
        np.asarray(gauss.fitted, dtype=np.float64),
        gauss.statistic,
        [p.centroid for p in gauss.peaks],
        fwhm,
        [p.area for p in gauss.peaks],
        replicas=replicas,
    )


def tail_text(test: TailTest) -> str:
    t = test.tail
    areas = "; ".join(f"{a:,.0f} ± {u:,.0f}" for a, u in zip(t.areas, t.area_uncs, strict=True))
    return (
        f"Low-energy tail test: {test.preferred} model preferred. {test.reason}. Tailed fit: "
        f"η = {t.eta:.3f} ± {t.eta_unc:.3f}, β = {t.beta:.2f} ± {t.beta_unc:.2f} channels, "
        f"areas (Gaussian + tail) {areas}. The Gaussian fit stays in the results unless you "
        "decide otherwise."
    )
