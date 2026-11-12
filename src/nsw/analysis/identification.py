"""Nuclide identification framework: which library nuclides are compatible with the
fitted peaks of an energy-calibrated spectrum.

The result is a ranked list of **candidates with their evidence**, never an
identification: a nuclide that is not in the library cannot be found, and no class
means "confirmed". The input energies must come from a verified energy calibration;
that gate is enforced by the service layer (``nsw.services.identification``).

Energy matching
---------------
A peak at E_peak matches a library line at E_line when

    |E_peak - E_line| <= k * sqrt(u_peak^2 + u_line^2 + u_cal^2 + u_extra^2)

u_peak   statistical uncertainty of the fitted position, in keV
u_line   uncertainty of the library energy (0, with a warning, if the source gives none)
u_cal    uncertainty of the calibration curve at the peak
u_extra  optional allowance set by the analyst (default 0)

k = 3 by default (``IdentificationSettings.k``). For normally distributed errors a true
line is then accepted with probability 99.73 %, i.e. about 1 true line in 370 is lost.
A larger k loses fewer true lines but the window, and with it the number of chance
coincidences, grows in proportion; k = 2 would lose 1 line in 22. The window does not
cover unmodelled effects (gain drift between calibration and measurement, peak-shape
bias); those belong into u_extra, stated by the analyst.

Evidence per nuclide
--------------------
Only lines with an absolute emission probability P >= ``min_emission_probability``
inside the analysed energy range are considered. The *anchor* is the matched line with
the most significant peak.

expected lines   With an efficiency curve: the predicted area of line j is
                 A_anchor * (P_j eps_j) / (P_anchor eps_anchor); the line is expected
                 when that exceeds ``missing_factor`` x the smallest detectable area at
                 E_j (supplied by the caller; otherwise the smallest observed peak
                 area). Without a curve: expected when P_j >= ``strong_ratio`` x P of
                 the strongest matched line. This ignores the energy dependence of the
                 efficiency and is labelled as such.
missing lines    expected but not matched: they count against the nuclide.
line fraction    W_matched / (W_matched + W_missing), weights w_j = P_j eps_j (with a
                 curve) or P_j (without).
intensity test   With a curve and >= 2 matched lines inside its range: the values
                 ln(A_j / (P_j eps_j)) must agree. Generalised least squares about their
                 mean with covariance diag(u_A^2/A^2 + u_P^2/P^2) + Cov(ln eps); the
                 chi-square has n - 1 degrees of freedom; inconsistent when its
                 probability is below ``intensity_p_min``. True coincidence summing is
                 not corrected and can make a real cascade emitter fail this test.
interference     A peak matched by lines of several nuclides is shared; this is listed
                 for every line. A nuclide whose peaks are all explained by one other
                 candidate that explains more peaks, and that is itself probable or
                 consistent, is called subsumed. A rejected candidate subsumes nothing.
peak counting    Peaks closer together than k combined energy uncertainties count as
                 one peak. Only peaks matched by lines of known emission probability
                 count towards the two peaks a class above POSSIBLE needs.
anchor area      When several lines of the nuclide fall on the anchor peak, its area is
                 attributed to their summed weight. If another nuclide shares the anchor
                 peak, predictions from it are too high; the shared peak is listed.
outside curve    Matched or unmatched lines outside the efficiency curve are not
                 weighed: they enter neither the line fraction nor the intensity test.
supplied peaks   A line is "missing" when no fitted peak was supplied at its energy.
                 The engine does not look into the spectrum: fit all peaks first.

Score (0 to 1) = line fraction x uniqueness x intensity factor, with
uniqueness = (W_unique + 0.5 W_shared) / W_matched over the matched lines of known
emission probability (weights P; by count if only relative lines match) and intensity
factor 0.5 when the
intensity test fails, 1 otherwise. The score only orders candidates of the same class.

Confidence classes
------------------
NOT_SUPPORTED  missing lines outweigh the matched ones (line fraction < 0.5), or a line
               expected to be stronger than the anchor is missing
POSSIBLE       a single matched peak (never more than this, whatever the nuclide);
               or relative intensities only; or some expected line missing; or the
               intensity test failed; or subsumed by another candidate
PROBABLE       >= 2 peaks matched, no expected line missing, intensities not testable
               (no efficiency curve, or fewer than two lines inside its range)
CONSISTENT     >= 2 peaks matched, no expected line missing, intensity test passed
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from nsw.analysis.distributions import chi2_sf
from nsw.analysis.efficiency import EfficiencyCurve
from nsw.core.errors import ProcessingError
from nsw.core.nuclear_data import GammaRecord, NuclideRecord

LIMITATION = (
    "Candidates are not identifications: nuclides absent from the library cannot be "
    "found, and agreement of energies does not exclude other emitters."
)


class Confidence(StrEnum):
    CONSISTENT = "consistent"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    NOT_SUPPORTED = "not supported"


_RANK = {
    Confidence.CONSISTENT: 0,
    Confidence.PROBABLE: 1,
    Confidence.POSSIBLE: 2,
    Confidence.NOT_SUPPORTED: 3,
}


@dataclass(frozen=True, slots=True)
class ObservedPeak:
    energy_kev: float
    position_unc_kev: float  # u_peak
    calibration_unc_kev: float  # u_cal
    area: float
    area_unc: float
    label: str = ""

    def __post_init__(self) -> None:
        values = (self.energy_kev, self.position_unc_kev, self.calibration_unc_kev)
        if not all(math.isfinite(v) for v in (*values, self.area, self.area_unc)):
            raise ProcessingError("peak values must be finite")
        if self.energy_kev <= 0.0 or self.position_unc_kev < 0.0 or self.calibration_unc_kev < 0.0:
            raise ProcessingError("peak energy must be positive and its uncertainties >= 0")
        if self.area_unc <= 0.0:
            raise ProcessingError("a peak area needs a positive uncertainty")

    @property
    def significance(self) -> float:
        return self.area / self.area_unc


@dataclass(frozen=True, slots=True)
class IdentificationSettings:
    k: float = 3.0  # coverage factor of the energy window
    min_emission_probability: float = 0.01  # weaker lines are not considered
    strong_ratio: float = 0.5  # without efficiency curve: expected if P >= ratio * P_max
    missing_factor: float = 2.0  # with curve: expected if predicted >= factor * detectable
    intensity_p_min: float = 1e-3
    extra_energy_unc_kev: float = 0.0
    energy_range_kev: tuple[float, float] | None = None  # None: span of the peaks

    def __post_init__(self) -> None:
        if not (math.isfinite(self.k) and 0.0 < self.k <= 10.0):
            raise ProcessingError("the coverage factor k must be in (0, 10]")
        if not 0.0 <= self.min_emission_probability < 1.0:
            raise ProcessingError("the minimum emission probability must be in [0, 1)")
        if not 0.0 < self.strong_ratio <= 1.0:
            raise ProcessingError("the strong-line ratio must be in (0, 1]")
        if not self.missing_factor >= 1.0:
            raise ProcessingError("the missing-line factor must be >= 1")
        if not 0.0 < self.intensity_p_min < 1.0:
            raise ProcessingError("the intensity-test probability must be in (0, 1)")
        if not (math.isfinite(self.extra_energy_unc_kev) and self.extra_energy_unc_kev >= 0.0):
            raise ProcessingError("the additional energy uncertainty must be >= 0")
        if self.energy_range_kev is not None and not (
            0.0 <= self.energy_range_kev[0] < self.energy_range_kev[1]
        ):
            raise ProcessingError("the energy range must be increasing")


@dataclass(frozen=True, slots=True)
class LineMatch:
    line: GammaRecord
    peak_index: int
    delta_kev: float  # E_peak - E_line
    sigma_kev: float  # combined standard uncertainty of the difference
    shared_with: tuple[str, ...] = ()  # other nuclides with a line on the same peak

    @property
    def z(self) -> float:
        return self.delta_kev / self.sigma_kev if self.sigma_kev > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class UnmatchedLine:
    line: GammaRecord
    expected: bool  # True: counts as missing
    predicted_area: float | None
    detectable_area: float | None
    reason: str


@dataclass(frozen=True, slots=True)
class IntensityTest:
    chi2: float
    ndf: int
    p_value: float
    consistent: bool
    energies_kev: tuple[float, ...]


@dataclass(frozen=True)
class Candidate:
    nuclide: str
    evaluation: str
    dataset_id: str
    confidence: Confidence
    score: float
    line_fraction: float
    uniqueness: float
    matches: tuple[LineMatch, ...]
    unmatched: tuple[UnmatchedLine, ...]
    intensity: IntensityTest | None
    reasons: tuple[str, ...]  # why this class
    warnings: tuple[str, ...]

    @property
    def missing(self) -> tuple[UnmatchedLine, ...]:
        return tuple(u for u in self.unmatched if u.expected)

    @property
    def matched_peaks(self) -> tuple[int, ...]:
        return tuple(sorted({m.peak_index for m in self.matches}))


@dataclass(frozen=True)
class IdentificationResult:
    candidates: tuple[Candidate, ...]  # ranked; only nuclides with at least one match
    unexplained_peaks: tuple[int, ...]  # indices of peaks no library line matches
    peaks: tuple[ObservedPeak, ...]
    settings: IdentificationSettings
    energy_range_kev: tuple[float, float]
    with_efficiency: bool
    not_evaluated: tuple[tuple[str, str], ...]  # (nuclide, reason)
    warnings: tuple[str, ...] = field(default=(LIMITATION,))

    def candidate(self, nuclide: str) -> Candidate | None:
        return next((c for c in self.candidates if c.nuclide == nuclide), None)


def detection_limit_counts(blank_variance: float) -> float:
    """Approximate smallest detectable net peak area above a continuum.

    Currie's detection limit for a net signal obtained as gross minus an equally
    uncertain blank, with 5 % probabilities for false detection and for missing a
    signal at the limit: L_D = k^2 + 2 k sqrt(2 B), k = 1.645, i.e. 2.71 + 4.65 sqrt(B),
    where B is the variance of the blank counts in the peak window (for raw counts: the
    counts themselves). L. A. Currie, Anal. Chem. 40 (1968) 586.

    Used here only to decide whether an absent library line counts as *missing*; it is
    not a reported detection limit of the measurement.
    """
    if not (math.isfinite(blank_variance) and blank_variance >= 0.0):
        raise ProcessingError("the blank variance must be >= 0")
    k = 1.6448536269514722
    return k * k + 2.0 * k * math.sqrt(2.0 * blank_variance)


# ---------------------------------------------------------------------- matching
def match_sigma(peak: ObservedPeak, line: GammaRecord, settings: IdentificationSettings) -> float:
    return math.sqrt(
        peak.position_unc_kev**2
        + (line.energy_unc_kev or 0.0) ** 2
        + peak.calibration_unc_kev**2
        + settings.extra_energy_unc_kev**2
    )


def match_line(
    line: GammaRecord, peaks: Sequence[ObservedPeak], settings: IdentificationSettings
) -> tuple[int, float, float] | None:
    """(peak index, E_peak - E_line, sigma) of the peak nearest in units of sigma, if
    any lies inside the window."""
    best: tuple[int, float, float] | None = None
    for i, peak in enumerate(peaks):
        delta = peak.energy_kev - line.energy_kev
        sigma = match_sigma(peak, line, settings)
        if abs(delta) > settings.k * sigma:
            continue
        if best is None or abs(delta) * best[2] < abs(best[1]) * sigma:
            best = (i, delta, sigma)
    return best


def peak_groups(peaks: Sequence[ObservedPeak], settings: IdentificationSettings) -> list[int]:
    """Group index per peak. Peaks whose energies differ by less than k combined standard
    uncertainties cannot be told apart by the matching window and form one group: two
    list entries for one physical peak must not count as two matched peaks."""
    order = sorted(range(len(peaks)), key=lambda i: peaks[i].energy_kev)
    groups = [0] * len(peaks)
    group = 0
    for previous, current in itertools.pairwise(order):
        a, b = peaks[previous], peaks[current]
        sigma = math.sqrt(
            a.position_unc_kev**2
            + a.calibration_unc_kev**2
            + b.position_unc_kev**2
            + b.calibration_unc_kev**2
            + 2.0 * settings.extra_energy_unc_kev**2
        )
        if b.energy_kev - a.energy_kev > settings.k * sigma:
            group += 1
        groups[current] = group
    return groups


def _efficiency(curve: EfficiencyCurve, energy: float) -> tuple[float, bool]:
    value, _, extrapolated = curve.efficiency(energy)
    return float(value[0]), bool(extrapolated[0])


def _intensity_test(
    matches: Sequence[LineMatch],
    peaks: Sequence[ObservedPeak],
    curve: EfficiencyCurve,
    p_min: float,
) -> IntensityTest | None:
    usable: list[LineMatch] = []
    per_peak: dict[int, int] = {}
    for m in matches:
        per_peak[m.peak_index] = per_peak.get(m.peak_index, 0) + 1
    for m in matches:
        peak = peaks[m.peak_index]
        if per_peak[m.peak_index] > 1 or peak.area <= 0.0:
            continue  # unresolved lines of this nuclide on one peak; non-detections
        if _efficiency(curve, m.line.energy_kev)[1]:
            continue  # outside the calibrated range of the efficiency curve
        usable.append(m)
    n = len(usable)
    if n < 2:
        return None
    energies = [m.line.energy_kev for m in usable]
    eff = np.array([_efficiency(curve, e)[0] for e in energies])
    area = np.array([peaks[m.peak_index].area for m in usable])
    rel_area = np.array([peaks[m.peak_index].area_unc for m in usable]) / area
    prob = np.array([m.line.emission_probability for m in usable])
    rel_prob = np.array([(m.line.intensity_unc or 0.0) for m in usable]) / prob
    y = np.log(area / (prob * eff))
    v = np.diag(rel_area**2 + rel_prob**2) + curve.relative_covariance(energies)
    try:
        v_inv = np.linalg.inv(v)
    except np.linalg.LinAlgError as exc:
        raise ProcessingError(
            "the intensity test has a singular covariance: peak areas and emission "
            "probabilities need uncertainties"
        ) from exc
    ones = np.ones(n)
    mean = float(ones @ v_inv @ y) / float(ones @ v_inv @ ones)
    r = y - mean
    chi2 = float(r @ v_inv @ r)
    p_value = float(chi2_sf(chi2, n - 1))
    return IntensityTest(chi2, n - 1, p_value, p_value >= p_min, tuple(energies))


def _evaluate(
    record: NuclideRecord,
    peaks: Sequence[ObservedPeak],
    settings: IdentificationSettings,
    energy_range: tuple[float, float],
    curve: EfficiencyCurve | None,
    detectable_area: Callable[[float], float] | None,
    groups: Sequence[int],
) -> Candidate | str:
    """Evidence for one record, or the reason why it was not evaluated. ``groups[i]`` is
    the index of the group of unresolvable peaks that peak ``i`` belongs to."""
    lo, hi = energy_range
    in_range = [line for line in record.lines if lo <= line.energy_kev <= hi]
    absolute = [
        line
        for line in in_range
        if line.is_absolute and line.intensity >= settings.min_emission_probability
    ]
    relative = [line for line in in_range if not line.is_absolute]
    if not absolute and not relative:
        return "no line of sufficient emission probability in the analysed energy range"
    warnings: list[str] = []
    if any(line.energy_unc_kev is None for line in absolute + relative):
        warnings.append("the library gives no uncertainty for some line energies (taken as 0)")
    if any(line.intensity_unc is None for line in absolute):
        warnings.append(
            "the library gives no uncertainty for some emission probabilities (taken as 0): "
            "the intensity test is stricter than the data justify"
        )

    matches: list[LineMatch] = []
    unmatched_lines: list[GammaRecord] = []
    for line in absolute + relative:
        found = match_line(line, peaks, settings)
        if found is None:
            unmatched_lines.append(line)
        else:
            matches.append(LineMatch(line, found[0], found[1], found[2]))
    if not matches:
        return "no line matches a peak"

    absolute_matches = [m for m in matches if m.line.is_absolute]
    reasons: list[str] = []
    unmatched: list[UnmatchedLine] = []
    intensity: IntensityTest | None = None
    w_matched = w_missing = 0.0
    strongest_missing = False

    if not absolute_matches:
        # relative intensities only: energies match, nothing else can be said
        for line in unmatched_lines:
            unmatched.append(
                UnmatchedLine(line, False, None, None, "not judged: no absolute intensities")
            )
        warnings.append(
            "matched lines have relative intensities without normalisation: no "
            "missing-line or intensity evidence"
        )
        line_fraction = math.nan
    else:
        anchor = max(absolute_matches, key=lambda m: peaks[m.peak_index].significance)
        anchor_peak = peaks[anchor.peak_index]
        p_max = max(m.line.intensity for m in absolute_matches)
        use_curve = curve is not None and not _efficiency(curve, anchor.line.energy_kev)[1]
        if curve is not None and not use_curve:
            warnings.append(
                "the anchor line lies outside the efficiency curve: lines judged by "
                "emission probability only"
            )
        floor = min((p.area for p in peaks if p.area > 0.0), default=math.nan)

        def weight(line: GammaRecord) -> float:
            if use_curve and curve is not None:
                eff, extrapolated = _efficiency(curve, line.energy_kev)
                return math.nan if extrapolated else line.intensity * eff
            return line.intensity

        # the anchor peak holds every line of this nuclide matched to it
        anchor_weight = sum(
            weight(m.line) for m in absolute_matches if m.peak_index == anchor.peak_index
        )
        if math.isnan(anchor_weight):
            anchor_weight = weight(anchor.line)
        unweighed = 0
        for m in absolute_matches:
            w = weight(m.line)
            if math.isnan(w):
                unweighed += 1  # outside the efficiency curve: cannot be weighed
            else:
                w_matched += w
        if unweighed:
            warnings.append(
                f"{unweighed} matched line(s) lie outside the efficiency curve and carry no "
                "weight in the line fraction"
            )
        for line in unmatched_lines:
            if not line.is_absolute:
                unmatched.append(UnmatchedLine(line, False, None, None, "relative intensity"))
                continue
            w = weight(line)
            if use_curve:
                if math.isnan(w):
                    unmatched.append(
                        UnmatchedLine(
                            line, False, None, None, "not judged: outside the efficiency curve"
                        )
                    )
                    continue
                predicted = anchor_peak.area * w / anchor_weight
                limit = detectable_area(line.energy_kev) if detectable_area else floor
                expected = math.isfinite(limit) and predicted >= settings.missing_factor * limit
                reason = (
                    f"predicted {predicted:.3g} counts, detectable from about {limit:.3g}"
                    if math.isfinite(limit)
                    else "no detection limit available"
                )
                unmatched.append(UnmatchedLine(line, expected, predicted, limit, reason))
            else:
                expected = line.intensity >= settings.strong_ratio * p_max
                reason = (
                    f"emission probability {line.intensity:.3g} against {p_max:.3g} of the "
                    "strongest matched line (efficiency not considered)"
                )
                unmatched.append(UnmatchedLine(line, expected, None, None, reason))
            if expected:
                w_missing += w
                if w >= anchor_weight:
                    strongest_missing = True
        line_fraction = w_matched / (w_matched + w_missing)
        if use_curve and curve is not None:
            intensity = _intensity_test(absolute_matches, peaks, curve, settings.intensity_p_min)
            warnings.append("true coincidence summing is not corrected in the intensity test")

    n_peaks = len({groups[m.peak_index] for m in matches})
    n_absolute = len({groups[m.peak_index] for m in absolute_matches})
    missing = [u for u in unmatched if u.expected]
    if absolute_matches and (line_fraction < 0.5 or strongest_missing):
        confidence = Confidence.NOT_SUPPORTED
        reasons.append(
            "a line expected to be stronger than the matched ones is missing"
            if strongest_missing
            else "the missing expected lines outweigh the matched ones"
        )
    elif n_peaks < 2:
        confidence = Confidence.POSSIBLE
        reasons.append("only one peak matches: a single coincidence cannot be distinguished")
    elif not absolute_matches:
        confidence = Confidence.POSSIBLE
        reasons.append("relative intensities only: evidence is limited to the energies")
    elif n_absolute < 2:
        confidence = Confidence.POSSIBLE
        reasons.append(
            "only one peak matches a line of known emission probability; the other matches "
            "are relative intensities, which add no evidence beyond the energy"
        )
    elif missing:
        confidence = Confidence.POSSIBLE
        reasons.append(f"{len(missing)} expected line(s) not found")
    elif intensity is not None and not intensity.consistent:
        confidence = Confidence.POSSIBLE
        reasons.append(
            f"peak areas do not follow the emission probabilities (chi-square "
            f"{intensity.chi2:.1f} for {intensity.ndf} degrees of freedom)"
        )
    elif intensity is not None:
        confidence = Confidence.CONSISTENT
        reasons.append(
            f"{n_peaks} peaks match, no expected line is missing, peak areas follow the "
            "emission probabilities"
        )
    else:
        confidence = Confidence.PROBABLE
        reasons.append(
            f"{n_peaks} peaks match and no expected line is missing; intensities could "
            "not be tested" + ("" if curve is not None else " (no efficiency curve)")
        )
    return Candidate(
        nuclide=record.nuclide,
        evaluation=record.provenance.evaluation,
        dataset_id=record.provenance.dataset_id,
        confidence=confidence,
        score=math.nan,  # set once interference between candidates is known
        line_fraction=line_fraction,
        uniqueness=math.nan,
        matches=tuple(matches),
        unmatched=tuple(unmatched),
        intensity=intensity,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def _with_interference(candidates: list[Candidate]) -> list[Candidate]:
    """Fill in shared peaks, uniqueness, score and the subsumed rule."""
    owners: dict[int, set[str]] = {}
    for c in candidates:
        for i in c.matched_peaks:
            owners.setdefault(i, set()).add(c.nuclide)
    peaks_of = {c.nuclide: set(c.matched_peaks) for c in candidates}
    # only a candidate that itself stands (probable or consistent) can explain another away
    supported = {
        c.nuclide: c.confidence in (Confidence.CONSISTENT, Confidence.PROBABLE) for c in candidates
    }
    out: list[Candidate] = []
    for c in candidates:
        matches = tuple(
            LineMatch(
                m.line,
                m.peak_index,
                m.delta_kev,
                m.sigma_kev,
                tuple(sorted(owners[m.peak_index] - {c.nuclide})),
            )
            for m in c.matches
        )
        # per-decay intensities only; relative ones have no common scale (then: count)
        weighed = [m for m in matches if m.line.is_absolute]
        weights = [m.line.intensity for m in weighed] if weighed else [1.0] * len(matches)
        weighed = weighed or list(matches)
        total = sum(weights)
        unique = sum(w for m, w in zip(weighed, weights, strict=True) if not m.shared_with)
        uniqueness = (unique + 0.5 * (total - unique)) / total
        confidence, reasons = c.confidence, list(c.reasons)
        mine = peaks_of[c.nuclide]
        covering = sorted(
            other
            for other, theirs in peaks_of.items()
            if other != c.nuclide
            and mine <= theirs
            and len(theirs) > len(mine)
            and supported[other]
        )
        if covering and confidence in (Confidence.CONSISTENT, Confidence.PROBABLE):
            confidence = Confidence.POSSIBLE
            reasons.append(
                "every matched peak is also explained by "
                + ", ".join(covering)
                + ", which explains more peaks"
            )
        fraction = 1.0 if math.isnan(c.line_fraction) else c.line_fraction
        factor = 0.5 if c.intensity is not None and not c.intensity.consistent else 1.0
        out.append(
            Candidate(
                nuclide=c.nuclide,
                evaluation=c.evaluation,
                dataset_id=c.dataset_id,
                confidence=confidence,
                score=fraction * uniqueness * factor,
                line_fraction=c.line_fraction,
                uniqueness=uniqueness,
                matches=matches,
                unmatched=c.unmatched,
                intensity=c.intensity,
                reasons=tuple(reasons),
                warnings=c.warnings,
            )
        )
    out.sort(key=lambda c: (_RANK[c.confidence], -c.score, -len(c.matched_peaks), c.nuclide))
    return out


def identify(
    peaks: Sequence[ObservedPeak],
    records: Sequence[NuclideRecord],
    settings: IdentificationSettings | None = None,
    *,
    efficiency: EfficiencyCurve | None = None,
    detectable_area: Callable[[float], float] | None = None,
) -> IdentificationResult:
    """Rank the nuclides of ``records`` by their compatibility with ``peaks``.

    ``records`` must hold one record per nuclide (choose the evaluation first, see
    ``ReferenceLibrary.select``). ``efficiency`` may be an absolute or a relative curve:
    only ratios between energies are used.
    """
    settings = settings or IdentificationSettings()
    if not peaks:
        raise ProcessingError("no peaks to identify: fit peaks first")
    if not records:
        raise ProcessingError("the reference library is empty")
    names = [r.nuclide for r in records]
    if len(set(names)) != len(names):
        raise ProcessingError(
            "more than one record per nuclide: choose one evaluation per nuclide before "
            "identifying (values of different evaluations are never combined)"
        )
    if len({r.provenance.fictitious for r in records}) > 1:
        raise ProcessingError("fictitious test records and real nuclear data cannot be mixed")
    if settings.energy_range_kev is not None:
        energy_range = settings.energy_range_kev
    else:
        energies = [p.energy_kev for p in peaks]
        widest = max(math.hypot(p.position_unc_kev, p.calibration_unc_kev) for p in peaks)
        margin = settings.k * math.hypot(widest, settings.extra_energy_unc_kev) + 1.0
        energy_range = (max(0.0, min(energies) - margin), max(energies) + margin)
    candidates: list[Candidate] = []
    not_evaluated: list[tuple[str, str]] = []
    groups = peak_groups(peaks, settings)
    for record in records:
        outcome = _evaluate(
            record, peaks, settings, energy_range, efficiency, detectable_area, groups
        )
        if isinstance(outcome, str):
            not_evaluated.append((record.nuclide, outcome))
        else:
            candidates.append(outcome)
    ranked = _with_interference(candidates)
    explained = {i for c in ranked for i in c.matched_peaks}
    warnings = [
        LIMITATION,
        "Lines are judged only against the peaks supplied: a line counts as missing when "
        "no fitted peak was passed at its energy, so fit every peak of the spectrum first.",
    ]
    if len(set(groups)) < len(peaks):
        warnings.append(
            "some peaks are closer together than their energy uncertainties resolve; they "
            "count as one peak"
        )
    if settings.energy_range_kev is None:
        warnings.append(
            "the analysed energy range was taken as the span of the fitted peaks: lines "
            "outside it were not considered, so missing lines beyond the outermost peaks "
            "are not penalised"
        )
    if efficiency is None:
        warnings.append(
            "no efficiency curve: expected lines are judged by emission probability "
            "alone and intensities are not tested; no candidate can exceed 'probable'"
        )
    return IdentificationResult(
        candidates=tuple(ranked),
        unexplained_peaks=tuple(i for i in range(len(peaks)) if i not in explained),
        peaks=tuple(peaks),
        settings=settings,
        energy_range_kev=energy_range,
        with_efficiency=efficiency is not None,
        not_evaluated=tuple(not_evaluated),
        warnings=tuple(warnings),
    )
