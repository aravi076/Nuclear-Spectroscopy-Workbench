"""Assisted energy calibration: suggest reference-line assignments and check them.

The analyst gives an *approximate* linear calibration (gain, offset and its relative
uncertainty) — from the detector set-up or a first guess, never from untrusted vendor
metadata applied silently. For every fitted peak, lines of the chosen library inside
the window |E_approx − E_line| ≤ k·σ, σ = √((r·E_approx)² + (g·u_ch)² + u_line²), are
listed, closest first. Only a peak with exactly one line in its window gets an automatic
suggestion; ambiguous peaks are reported for the analyst to decide. Suggestions are
proposals: the analyst ticks or edits them before fitting, and for measured spectra the
resulting calibration stays PROVISIONAL (source identities are not verified).

Checks before fitting (``assignment_problems``): one line given to two peaks, two
lines on one peak, too few points for the order, poor energy coverage. After fitting
(``outlier_messages``): points with |normalised residual| > 3.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from nsw.core.calibration import CalibrationPoint
from nsw.core.errors import ProcessingError
from nsw.core.nuclear_data import ReferenceLibrary
from nsw.core.spectrum import Spectrum
from nsw.services import identification as ids
from nsw.services.calibration import CalibrationFit, PeakRow

DEFAULT_K = 3.0
DEFAULT_REL_UNC = 0.01  # 1 % of the energy: a modest first guess of the gain
MIN_COVERAGE = 0.3  # points should span at least this fraction of the spectrum's energies
OUTLIER_SIGMA = 3.0


@dataclass(frozen=True, slots=True)
class LineSuggestion:
    nuclide: str
    evaluation: str
    energy_kev: float
    energy_unc_kev: float
    distance_sigma: float  # |E_approx − E_line| / σ

    @property
    def source(self) -> str:
        return f"{self.nuclide} ({self.evaluation}) {self.energy_kev:g} keV"


@dataclass(frozen=True, slots=True)
class PeakSuggestions:
    row_index: int
    channel: float
    approx_energy_kev: float
    lines: tuple[LineSuggestion, ...]  # inside the window, closest first

    @property
    def unique(self) -> LineSuggestion | None:
        return self.lines[0] if len(self.lines) == 1 else None


def library_missing(spectrum: Spectrum, library: ReferenceLibrary) -> tuple[str, ...]:
    """Library kind rules (as everywhere): fictitious for synthetic, real for measured."""
    if not library.records:
        return ("a reference library with records",)
    if library.fictitious and not spectrum.is_synthetic:
        return ("a real reference library: the fictitious one must not be used on measurements",)
    if not library.fictitious and spectrum.is_synthetic:
        return ("the fictitious library: synthetic spectra contain invented lines",)
    return ()


def suggest(
    rows: Sequence[PeakRow],
    library: ReferenceLibrary,
    gain_kev_per_channel: float,
    offset_kev: float,
    rel_uncertainty: float = DEFAULT_REL_UNC,
    k: float = DEFAULT_K,
    min_fraction: float = 0.05,
) -> list[PeakSuggestions]:
    if not gain_kev_per_channel > 0.0:
        raise ProcessingError("the approximate gain must be positive (keV per channel)")
    if not 0.0 < rel_uncertainty < 0.5:
        raise ProcessingError(
            "the relative uncertainty of the approximate calibration must be in (0, 0.5)"
        )
    if not k > 0.0:
        raise ProcessingError("k must be positive")
    lines = []
    for record in ids.select_records(library):
        if not record.lines:
            continue
        strongest = max(g.intensity for g in record.lines)
        for g in record.lines:
            if g.intensity >= min_fraction * strongest:
                lines.append((record, g))
    out = []
    for i, row in enumerate(rows):
        e = offset_kev + gain_kev_per_channel * row.centroid
        found = []
        for record, g in lines:
            sigma = math.sqrt(
                (rel_uncertainty * e) ** 2
                + (gain_kev_per_channel * row.centroid_unc) ** 2
                + (g.energy_unc_kev or 0.0) ** 2
            )
            d = abs(e - g.energy_kev) / sigma
            if d <= k:
                found.append(
                    LineSuggestion(
                        record.nuclide,
                        record.provenance.evaluation,
                        g.energy_kev,
                        g.energy_unc_kev or 0.0,
                        d,
                    )
                )
        found.sort(key=lambda s: s.distance_sigma)
        out.append(PeakSuggestions(i, row.centroid, e, tuple(found)))
    return out


def suggestion_summary(suggestions: Sequence[PeakSuggestions]) -> str:
    unique = sum(1 for s in suggestions if s.unique is not None)
    ambiguous = [s for s in suggestions if len(s.lines) > 1]
    none = sum(1 for s in suggestions if not s.lines)
    text = f"{unique} peak(s) with one compatible line (filled in, please review)"
    if ambiguous:
        detail = "; ".join(
            f"channel {s.channel:.1f}: " + ", ".join(line.source for line in s.lines[:3])
            for s in ambiguous
        )
        text += f"; {len(ambiguous)} ambiguous, decide yourself ({detail})"
    if none:
        text += f"; {none} without a compatible line"
    return text + "."


def assignment_problems(
    points: Sequence[CalibrationPoint], spectrum: Spectrum, order: int, approx_gain: float | None
) -> list[str]:
    """Reasons to reconsider the ticked assignments before fitting (empty: none found)."""
    problems = []
    energies = Counter(round(p.reference_value, 6) for p in points)
    for energy, n in energies.items():
        if n > 1:
            problems.append(f"the reference energy {energy:g} keV is given to {n} peaks")
    channels = Counter(round(p.channel, 3) for p in points)
    for channel, n in channels.items():
        if n > 1:
            problems.append(f"the peak at channel {channel:g} has {n} reference energies")
    needed = max(2, order + 1)
    if len(points) < needed:
        problems.append(f"order {order} needs at least {needed} points, {len(points)} ticked")
    if approx_gain is not None and len(points) >= 2:
        span = max(p.channel for p in points) - min(p.channel for p in points)
        if span < MIN_COVERAGE * spectrum.n_channels:
            problems.append(
                f"the points cover {span / spectrum.n_channels:.0%} of the channels: energies "
                "far from them will be extrapolated"
            )
    return problems


def outlier_messages(fit: CalibrationFit) -> list[str]:
    out = []
    for p, r in zip(fit.points, fit.normalised_residuals, strict=True):
        if abs(r) > OUTLIER_SIGMA:
            out.append(
                f"channel {p.channel:.2f} ({p.reference_value:g} keV, {p.reference_source}) "
                f"deviates by {r:+.1f} σ: check the assignment"
            )
    return out
