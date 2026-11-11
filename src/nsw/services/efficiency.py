"""Use case: efficiency calibration and activity, with the gate for experimental data.

Policy
------
No efficiency and no activity is computed for *experimental* spectra while
``EXPERIMENTAL_QUANTIFICATION_ENABLED`` is False. The laboratory dataset has no source
certificates (activity, reference date, uncertainty), no recorded geometry, no detector
model, and no agreed source of nuclear data. :func:`missing_inputs` lists what is
absent for a given spectrum; the calculation functions refuse experimental spectra
with that list. Synthetic spectra are processed and every result is labelled
synthetic.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from nsw.analysis.activity import ActivityResult, LineActivity, combine_lines, line_activity
from nsw.analysis.efficiency import (
    EfficiencyCurve,
    EfficiencyPoint,
    GammaLine,
    MeasurementTiming,
    ReferenceSource,
    efficiency_point,
    fit_efficiency_curve,
)
from nsw.core.calibration import CalibrationStatus
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum

__all__ = [
    "EXPERIMENTAL_QUANTIFICATION_ENABLED",
    "ActivityResult",
    "EfficiencyCurve",
    "EfficiencyPoint",
    "GammaLine",
    "MeasurementTiming",
    "ProcessingError",
    "ReferenceSource",
    "activity",
    "curve_rows",
    "efficiency_curve",
    "missing_inputs",
    "result_label",
    "timing_of",
]

EXPERIMENTAL_QUANTIFICATION_ENABLED = False
SYNTHETIC_LABEL = "SYNTHETIC — arbitrary test source and efficiency; not a measurement"
Rows = list[tuple[str, str]]


def missing_inputs(spectrum: Spectrum) -> tuple[str, ...]:
    """What prevents an absolute efficiency or activity for this spectrum."""
    if spectrum.is_synthetic:
        return ()
    missing = [
        "certified source activity with reference date and uncertainty",
        "source-detector geometry (distance, source form, absorbers)",
        "detector model and crystal dimensions",
        "nuclear decay data (half-lives, emission probabilities) with a recorded source",
    ]
    if spectrum.calibration_status is not CalibrationStatus.VERIFIED:
        missing.append("verified energy calibration (line assignments are unconfirmed)")
    if spectrum.start_time is None:
        missing.append("acquisition start time")
    if spectrum.live_time_s is None or spectrum.real_time_s is None:
        missing.append("live and real time")
    if not EXPERIMENTAL_QUANTIFICATION_ENABLED:
        missing.append("authorisation by the project owner once the above are supplied")
    return tuple(missing)


def _require_allowed(spectrum: Spectrum, what: str) -> None:
    missing = missing_inputs(spectrum)
    if missing:
        raise ProcessingError(
            f"{what} is not available for this experimental spectrum. Missing: "
            + "; ".join(missing)
        )


def result_label(spectrum: Spectrum) -> str:
    return SYNTHETIC_LABEL if spectrum.is_synthetic else "EXPERIMENTAL"


def timing_of(spectrum: Spectrum, start_time: datetime | None = None) -> MeasurementTiming:
    """Timing of a spectrum. Synthetic spectra carry no start time: it must be given."""
    start = start_time if start_time is not None else spectrum.start_time
    if start is None:
        raise ProcessingError("the start time of the measurement is needed")
    if spectrum.live_time_s is None or spectrum.real_time_s is None:
        raise ProcessingError("live and real time are needed")
    return MeasurementTiming(start, spectrum.live_time_s, spectrum.real_time_s)


def efficiency_curve(
    spectrum: Spectrum,
    areas: Sequence[tuple[float, float]],
    lines: Sequence[GammaLine],
    source: ReferenceSource,
    timing: MeasurementTiming,
    order: int = 2,
    geometry: str = "",
) -> EfficiencyCurve:
    """Absolute efficiency curve from fitted peak areas of one reference-source spectrum."""
    _require_allowed(spectrum, "An absolute efficiency calibration")
    if spectrum.is_synthetic and not source.synthetic:
        raise ProcessingError("a synthetic spectrum needs a source marked as synthetic")
    if len(areas) != len(lines):
        raise ProcessingError("one gamma line per peak area is required")
    points = [
        efficiency_point(area, unc, line, source, timing)
        for (area, unc), line in zip(areas, lines, strict=True)
    ]
    return fit_efficiency_curve(points, order, geometry=geometry)


def activity(
    spectrum: Spectrum,
    areas: Sequence[tuple[float, float]],
    lines: Sequence[GammaLine],
    curve: EfficiencyCurve,
    timing: MeasurementTiming,
    half_life_s: float,
    half_life_unc_s: float = 0.0,
    reference_time: datetime | None = None,
) -> ActivityResult:
    """Activity at ``reference_time`` (default: start of the measurement)."""
    _require_allowed(spectrum, "An activity")
    if len(areas) != len(lines):
        raise ProcessingError("one gamma line per peak area is required")
    reference = reference_time if reference_time is not None else timing.start_time
    results: list[LineActivity] = [
        line_activity(area, unc, line, curve, timing, half_life_s, reference)
        for (area, unc), line in zip(areas, lines, strict=True)
    ]
    return combine_lines(results, curve, half_life_unc_s, reference)


def curve_rows(spectrum: Spectrum, curve: EfficiencyCurve) -> Rows:
    lo, hi = curve.energy_range_kev
    kind = "absolute full-energy-peak efficiency" if curve.absolute else "relative efficiency"
    rows: Rows = [
        ("Status", result_label(spectrum)),
        ("Quantity", kind),
        ("Geometry", curve.geometry or "not recorded"),
        (
            "Model",
            f"ln ε = Σ a_k·ln(E/E₀)^k, order {curve.order}, E₀ = "
            f"{curve.reference_energy_kev:.2f} keV; {len(curve.points)} points",
        ),
        ("χ² / ndf", f"{curve.chi2:.2f} / {curve.ndf}"),
        ("Valid range", f"{lo:.1f}–{hi:.1f} keV; outside this range values are extrapolated"),
    ]
    for energy in (lo, (lo * hi) ** 0.5, hi):
        value, rel, _ = curve.efficiency(energy)
        rows.append((f"ε at {energy:.1f} keV", f"{value[0]:.4g} ± {100 * rel[0]:.2g} %"))
    rows.extend(("Caution", w) for w in curve.warnings)
    return rows
