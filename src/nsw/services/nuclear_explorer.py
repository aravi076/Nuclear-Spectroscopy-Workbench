"""Nuclear Data Explorer: browse reference libraries, overlay lines, match a fitted peak.

Rules (docs/design/UX_REDESIGN_PLAN.md §7, docs/science/nuclear_data.md):

- Only libraries loaded in the session are shown: the fictitious test library, and
  reference-data files the user imports. NSW bundles no real nuclear data.
- Every record is shown with all its provenance fields; relative intensities are
  labelled as such and never presented as emission probabilities; records of different
  evaluations for one nuclide are listed side by side (``conflict_table``), never merged.
- Reference lines are drawn on a spectrum only when ``overlay_missing`` is empty: a
  VERIFIED energy calibration, experimental verification enabled for measured spectra,
  and a library of the right kind (fictitious for synthetic spectra, real for measured
  ones). Positions are converted to channels with the spectrum's calibration, so the
  plot keeps its channel axis.
- ``match_peak`` lists the lines inside the window |ΔE| ≤ k·σ for one fitted peak,
  σ = √(u_peak² + u_line² + u_cal²) (k = 3 by default), with the other strong lines of
  each nuclide and whether a fitted peak was found for them. It ranks; it does not
  identify (the Nuclide Identification page weighs all evidence).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from nsw.analysis.fitting import FitResult
from nsw.analysis.identification import IdentificationSettings, ObservedPeak, match_sigma
from nsw.core.errors import ProcessingError
from nsw.core.nuclear_data import GammaRecord, NuclideRecord, ReferenceLibrary
from nsw.core.spectrum import Spectrum
from nsw.services import identification as ids

Rows = list[tuple[str, str]]
Table = tuple[list[str], list[list[str]]]
STRONG_FRACTION = 0.5  # "other strong lines": intensity >= this fraction of the strongest


def _pm(value: float | None, unc: float | None, fmt: str) -> str:
    if value is None:
        return "—"
    text = format(value, fmt)
    return text if unc is None else f"{text} ± {format(unc, fmt)}"


def _intensity_text(line: GammaRecord) -> str:
    value = _pm(line.intensity, line.intensity_unc, ".4g")
    return value + (" /decay" if line.is_absolute else " (relative)")


def _strength(line: GammaRecord) -> float:
    return line.intensity  # same convention within one record, so comparable


# ---------------------------------------------------------------------- browsing
def library_label(library: ReferenceLibrary) -> str:
    kind = "fictitious test library" if library.fictitious else ", ".join(library.evaluations)
    return f"{library.name} ({kind}; {len(library.nuclides)} nuclides)"


def nuclide_table(library: ReferenceLibrary) -> Table:
    headers = ["Nuclide", "Evaluation", "Half-life", "Lines", "Intensities", "Retrieved"]
    rows = []
    for r in library.records:
        half = r.half_life_as_stated or (
            _pm(r.half_life_s, r.half_life_unc_s, ".6g") + " s" if r.half_life_s else "—"
        )
        rows.append(
            [
                r.nuclide,
                r.provenance.evaluation,
                half,
                str(len(r.lines)),
                "relative (not normalised)" if r.has_relative_lines else "photons per decay",
                r.provenance.retrieval_date.isoformat(),
            ]
        )
    return headers, rows


def line_table(
    record: NuclideRecord,
    min_fraction: float = 0.0,
    energy_range_kev: tuple[float, float] | None = None,
) -> Table:
    """Lines of one record, optionally only those above a fraction of the strongest."""
    headers = ["Energy (keV)", "Intensity", "Origin", "Transition", "Follows"]
    if not record.lines:
        return headers, []
    strongest = max(_strength(line) for line in record.lines)
    rows = []
    for line in sorted(record.lines, key=lambda g: g.energy_kev):
        if _strength(line) < min_fraction * strongest:
            continue
        if energy_range_kev is not None and not (
            energy_range_kev[0] <= line.energy_kev <= energy_range_kev[1]
        ):
            continue
        rows.append(
            [
                _pm(line.energy_kev, line.energy_unc_kev, ".3f"),
                _intensity_text(line),
                line.origin.value,
                line.transition or "—",
                line.emitted_by or "—",
            ]
        )
    return headers, rows


def provenance_rows(record: NuclideRecord) -> Rows:
    p = record.provenance
    rows: Rows = [
        ("Nuclide", record.nuclide),
        ("Evaluation", p.evaluation),
        ("Dataset identifier", p.dataset_id),
        ("Evaluation date", p.evaluation_date or "not stated"),
        ("Retrieved", p.retrieval_date.isoformat()),
        ("Accessed through", p.accessed_through or "the evaluator's own source"),
        ("Citation", p.citation),
        ("Licence / reuse terms", p.licence),
        ("Reuse status", p.reuse_status.value),
        ("URL", p.url or "—"),
        ("Imported with", p.parser),
        ("Transformations", "; ".join(p.transformations) or "none"),
        ("Half-life as stated", record.half_life_as_stated or "—"),
        (
            "Decay branches",
            "; ".join(
                f"{b.mode} → {b.daughter or '?'}"
                + (f" ({_pm(b.fraction, b.fraction_unc, '.4g')})" if b.fraction else "")
                for b in record.branches
            )
            or "—",
        ),
    ]
    if record.has_relative_lines:
        rows.append(
            (
                "Intensity convention",
                "RELATIVE: not emission probabilities; not usable for efficiency or activity",
            )
        )
    if record.comment:
        rows.append(("Comment", record.comment))
    return rows


def conflict_table(library: ReferenceLibrary) -> Table:
    headers = ["Nuclide", "Evaluations", "Differences (not reconciled)"]
    rows = [
        [c.nuclide, ", ".join(c.evaluations), "; ".join(c.differences)] for c in library.conflicts()
    ]
    return headers, rows


# ---------------------------------------------------------------------- overlay
def overlay_missing(spectrum: Spectrum, library: ReferenceLibrary) -> tuple[str, ...]:
    """Why reference lines cannot be drawn on this spectrum (empty: they can)."""
    return ids.missing_inputs(spectrum, library)


def channels_for_energies(spectrum: Spectrum, energies_kev: Sequence[float]) -> list[float | None]:
    """Channel positions of energies with the spectrum's verified calibration.

    The polynomial is inverted numerically over the spectrum's channels; it must increase
    with channel there. Energies outside the spectrum give None.
    """
    cal = spectrum.energy_calibration
    if cal is None or not cal.is_verified:
        raise ProcessingError("reference lines need a verified energy calibration")
    lo = spectrum.first_channel - 0.5
    hi = spectrum.first_channel + spectrum.n_channels - 0.5
    grid = np.linspace(lo, hi, 8 * spectrum.n_channels + 1)
    energy = cal.energy_at(grid)
    if np.any(np.diff(energy) <= 0.0):
        raise ProcessingError("the calibration does not increase with channel over the spectrum")
    out: list[float | None] = []
    for e in energies_kev:
        if energy[0] <= e <= energy[-1]:
            out.append(float(np.interp(e, energy, grid)))
        else:
            out.append(None)
    return out


@dataclass(frozen=True, slots=True)
class OverlayLine:
    nuclide: str
    energy_kev: float
    channel: float
    relative_strength: float  # intensity / strongest line of the record (0..1]
    label: str


def overlay_lines(
    spectrum: Spectrum,
    library: ReferenceLibrary,
    nuclides: Iterable[str],
    min_fraction: float = 0.05,
) -> list[OverlayLine]:
    """Lines of the chosen nuclides (preferred evaluation each) positioned in channels."""
    missing = overlay_missing(spectrum, library)
    if missing:
        raise ProcessingError("reference lines cannot be drawn: " + "; ".join(missing))
    wanted = set(nuclides)
    out: list[OverlayLine] = []
    for record in ids.select_records(library):
        if record.nuclide not in wanted or not record.lines:
            continue
        strongest = max(_strength(g) for g in record.lines)
        lines = [g for g in record.lines if _strength(g) >= min_fraction * strongest]
        channels = channels_for_energies(spectrum, [g.energy_kev for g in lines])
        for g, ch in zip(lines, channels, strict=True):
            if ch is None:
                continue
            out.append(
                OverlayLine(
                    record.nuclide,
                    g.energy_kev,
                    ch,
                    _strength(g) / strongest,
                    f"{record.nuclide} {g.energy_kev:.1f} keV",
                )
            )
    return out


# ---------------------------------------------------------------------- matching
MATCH_HEADERS = (
    "Nuclide",
    "Line (keV)",
    "Intensity",
    "ΔE (keV)",
    "ΔE / σ",
    "Other strong lines found",
    "Evaluation",
)


@dataclass(frozen=True, slots=True)
class LineMatch:
    nuclide: str
    evaluation: str
    line: GammaRecord
    delta_kev: float
    sigma_kev: float
    other_strong: int  # other strong lines of the nuclide inside the spectrum's range
    other_found: int  # ... of which a fitted peak lies inside the window

    @property
    def normalised(self) -> float:
        return self.delta_kev / self.sigma_kev if self.sigma_kev > 0 else math.inf


def match_peak(
    spectrum: Spectrum,
    fits: Sequence[FitResult],
    peak_channel: float,
    library: ReferenceLibrary,
    settings: IdentificationSettings | None = None,
) -> tuple[ObservedPeak, list[LineMatch]]:
    """Reference lines compatible with the fitted peak nearest ``peak_channel``."""
    settings = settings or IdentificationSettings()
    missing = overlay_missing(spectrum, library)
    if missing:
        raise ProcessingError("line matching refused: " + "; ".join(missing))
    peaks = ids.observed_peaks(spectrum, fits)
    if not peaks:
        raise ProcessingError("fit the peak first: matching uses fitted positions")
    cal = spectrum.energy_calibration
    assert cal is not None  # noqa: S101 - type narrowing: overlay_missing requires it
    target_e = float(cal.energy_at(peak_channel))
    peak = min(peaks, key=lambda p: abs(p.energy_kev - target_e))
    e_lo = float(cal.energy_at(spectrum.first_channel))
    e_hi = float(cal.energy_at(spectrum.first_channel + spectrum.n_channels - 1))
    matches = []
    for record in ids.select_records(library):
        if not record.lines:
            continue
        strongest = max(_strength(g) for g in record.lines)
        for line in record.lines:
            sigma = match_sigma(peak, line, settings)
            delta = peak.energy_kev - line.energy_kev
            if abs(delta) > settings.k * sigma:
                continue
            others = [
                g
                for g in record.lines
                if g is not line
                and _strength(g) >= STRONG_FRACTION * strongest
                and e_lo <= g.energy_kev <= e_hi
            ]
            found = sum(
                1
                for g in others
                if any(
                    abs(p.energy_kev - g.energy_kev) <= settings.k * match_sigma(p, g, settings)
                    for p in peaks
                    if p is not peak
                )
            )
            matches.append(
                LineMatch(
                    record.nuclide,
                    record.provenance.evaluation,
                    line,
                    delta,
                    sigma,
                    len(others),
                    found,
                )
            )
    matches.sort(key=lambda m: (-(m.other_found / m.other_strong if m.other_strong else 0.0),
                                abs(m.normalised)))  # fmt: skip
    return peak, matches


def match_rows(matches: Sequence[LineMatch]) -> list[list[str]]:
    return [
        [
            m.nuclide,
            _pm(m.line.energy_kev, m.line.energy_unc_kev, ".3f"),
            _intensity_text(m.line),
            f"{m.delta_kev:+.3f}",
            f"{m.normalised:+.2f}",
            f"{m.other_found} of {m.other_strong}" if m.other_strong else "none expected",
            m.evaluation,
        ]
        for m in matches
    ]
