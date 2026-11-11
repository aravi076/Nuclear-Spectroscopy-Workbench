"""Use case: nuclide candidates for a spectrum, with the calibration gate, the choice of
evaluation and the presentation of the evidence.

Gates
-----
1. **Verified energy calibration.** A spectrum whose calibration status is not VERIFIED
   is refused: channel numbers, a vendor calibration or a provisional calibration cannot
   identify a nuclide, and neither can a file or folder name. Every spectrum of the
   laboratory dataset is UNVERIFIED (``services.calibration``), so none of them can be
   processed here until that changes.
2. **Fictitious and real data stay apart.** The fictitious test library is accepted
   only for synthetic spectra, real nuclear data only for experimental spectra.
3. **One evaluation per nuclide, chosen explicitly.** The default order of preference
   is DDEP, then ENSDF; values of the two are never combined.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import numpy as np

import nsw
from nsw.analysis.efficiency import EfficiencyCurve
from nsw.analysis.fitting import FitResult
from nsw.analysis.identification import (
    LIMITATION,
    Candidate,
    Confidence,
    IdentificationResult,
    IdentificationSettings,
    ObservedPeak,
    detection_limit_counts,
)
from nsw.analysis.identification import identify as _identify
from nsw.core.calibration import CalibrationStatus, EnergyCalibration
from nsw.core.errors import ProcessingError
from nsw.core.nuclear_data import NuclideRecord, ReferenceLibrary
from nsw.core.spectrum import Spectrum
from nsw.io.reference_data import KNOWN_SOURCES, JsonFileProvider, load_library
from nsw.services import calibration as calibration_service
from nsw.services.calibration import peak_rows
from nsw.services.export import ExportError, ensure_output_allowed
from nsw.services.peaks import spectrum_name
from nsw.simulation.reference import fictitious_library

__all__ = [
    "DEFAULT_PREFERENCE",
    "LIMITATION",
    "Candidate",
    "Confidence",
    "IdentificationResult",
    "IdentificationSettings",
    "ProcessingError",
    "ReferenceLibrary",
    "candidate_table",
    "evidence_rows",
    "export_identification_csv",
    "fictitious_library",
    "identification_csv_text",
    "identify",
    "library_table",
    "load_reference_file",
    "missing_inputs",
    "observed_peaks",
    "result_label",
    "select_records",
    "sources_table",
]

DEFAULT_PREFERENCE: tuple[str, ...] = ("DDEP", "ENSDF")
FICTITIOUS_PREFERENCE: tuple[str, ...] = ("FICTITIOUS",)
WINDOW_FWHM = 2.5  # width of the window for the detectable-area estimate
Rows = list[tuple[str, str]]
Table = tuple[list[str], list[list[str]]]


def missing_inputs(spectrum: Spectrum, library: ReferenceLibrary | None) -> tuple[str, ...]:
    """What prevents an identification for this spectrum (empty: nothing)."""
    missing: list[str] = []
    if spectrum.calibration_status is not CalibrationStatus.VERIFIED:
        missing.append(
            "verified energy calibration (status: "
            f"{spectrum.calibration_status.value}); channel numbers and file or folder "
            "names cannot identify a nuclide"
        )
    if not spectrum.is_synthetic and not calibration_service.EXPERIMENTAL_VERIFICATION_ENABLED:
        missing.append(
            "authorisation: verified energy calibrations are not enabled for experimental "
            "spectra, so no experimental spectrum can be processed here"
        )
    if library is None or not library.records:
        missing.append(
            "reference library: NSW contains no nuclear data; import an NSW reference-data "
            "file with recorded provenance"
        )
    elif library.fictitious and not spectrum.is_synthetic:
        missing.append(
            "nuclear reference data: the loaded library is the fictitious test library, "
            "which must not be applied to measurements"
        )
    elif not library.fictitious and spectrum.is_synthetic:
        missing.append(
            "fictitious test library: synthetic spectra contain invented lines and are "
            "not compared with real nuclear data"
        )
    return tuple(missing)


def result_label(spectrum: Spectrum) -> str:
    if spectrum.is_synthetic:
        return "SYNTHETIC — fictitious nuclides; software verification only"
    return "EXPERIMENTAL — candidates only, not an identification"


def load_reference_file(path: Path) -> ReferenceLibrary:
    """Read an NSW reference-data file (see docs/nuclear_data.md)."""
    return load_library(Path(path).stem, [JsonFileProvider(Path(path))])


def select_records(
    library: ReferenceLibrary, preference: Sequence[str] | None = None
) -> tuple[NuclideRecord, ...]:
    if preference is None:
        preference = FICTITIOUS_PREFERENCE if library.fictitious else DEFAULT_PREFERENCE
    return library.select(preference)


# ---------------------------------------------------------------------- peaks
def _calibration(spectrum: Spectrum) -> EnergyCalibration:
    cal = spectrum.energy_calibration
    if cal is None or not cal.is_verified:
        raise ProcessingError(
            "Nuclide identification needs a verified energy calibration; this spectrum "
            f"has calibration status {spectrum.calibration_status.value!r}. Channel "
            "numbers cannot identify a nuclide."
        )
    return cal


def observed_peaks(spectrum: Spectrum, fits: Iterable[FitResult]) -> list[ObservedPeak]:
    """Fitted peaks with energies and their two energy uncertainties.

    u_peak = gain x uncertainty of the fitted position; u_cal from the covariance of the
    calibration (0 for exact synthetic truth, which has none). Peaks of poor fits are
    used: their positions remain meaningful, and they are marked in the label.
    """
    cal = _calibration(spectrum)
    peaks: list[ObservedPeak] = []
    for row in peak_rows(fits):
        gain = float(cal.gain_at(row.centroid))
        u_cal = (
            float(cal.energy_uncertainty_at(row.centroid)) if cal.covariance is not None else 0.0
        )
        peaks.append(
            ObservedPeak(
                energy_kev=float(cal.energy_at(row.centroid)),
                position_unc_kev=abs(gain) * row.centroid_unc,
                calibration_unc_kev=u_cal,
                area=row.area,
                area_unc=row.area_unc,
                label=f"channel {row.centroid:.2f}"
                + (" (POOR FIT)" if row.quality != "ok" else ""),
            )
        )
    return peaks


def _detectable_area(
    spectrum: Spectrum, cal: EnergyCalibration, fits: Sequence[FitResult]
) -> Callable[[float], float]:
    """Smallest detectable peak area at an energy, from the counts actually present in a
    window of ``WINDOW_FWHM`` FWHM there (width interpolated between the fitted peaks)."""
    channels = spectrum.channels.astype(np.float64)
    energies = cal.energy_at(channels)
    variance = spectrum.variance_array()
    rows = peak_rows(fits)
    centres = np.array([r.centroid for r in rows])
    widths = np.array([r.fwhm for r in rows])

    def limit(energy_kev: float) -> float:
        if not energies[0] <= energy_kev <= energies[-1]:
            return math.inf
        channel = float(np.interp(energy_kev, energies, channels))
        fwhm = float(np.interp(channel, centres, widths))
        half = 0.5 * WINDOW_FWHM * fwhm
        lo = max(0, round(channel - half) - int(channels[0]))
        hi = min(len(variance), round(channel + half) - int(channels[0]) + 1)
        return detection_limit_counts(float(variance[lo:hi].sum()))

    return limit


def identify(
    spectrum: Spectrum,
    fits: Sequence[FitResult],
    library: ReferenceLibrary,
    settings: IdentificationSettings | None = None,
    *,
    preference: Sequence[str] | None = None,
    efficiency: EfficiencyCurve | None = None,
) -> IdentificationResult:
    """Nuclide candidates for the fitted peaks of ``spectrum``."""
    missing = missing_inputs(spectrum, library)
    if missing:
        raise ProcessingError(
            "Nuclide identification is not available for this spectrum. Missing: "
            + "; ".join(missing)
        )
    cal = _calibration(spectrum)
    if any(f.spectrum_sha256 != spectrum.data_sha256 for f in fits):
        raise ProcessingError("the fitted peaks belong to a different spectrum")
    peaks = observed_peaks(spectrum, fits)
    if not peaks:
        raise ProcessingError("no usable fitted peaks: fit peaks first")
    records = select_records(library, preference)
    if not records:
        raise ProcessingError(
            "the library has no record from the selected evaluations "
            f"({', '.join(preference or DEFAULT_PREFERENCE)}); it contains "
            f"{', '.join(library.evaluations)}"
        )
    settings = settings or IdentificationSettings()
    if settings.energy_range_kev is None:
        # the whole spectrum: restricting to the span of the calibration points would
        # drop the outermost calibration lines themselves by rounding
        span = (float(spectrum.channels[0]), float(spectrum.channels[-1]))
        lo, hi = (float(cal.energy_at(c)) for c in span)
        settings = IdentificationSettings(
            k=settings.k,
            min_emission_probability=settings.min_emission_probability,
            strong_ratio=settings.strong_ratio,
            missing_factor=settings.missing_factor,
            intensity_p_min=settings.intensity_p_min,
            extra_energy_unc_kev=settings.extra_energy_unc_kev,
            energy_range_kev=(max(lo, 0.0), hi),
        )
    return _identify(
        peaks,
        records,
        settings,
        efficiency=efficiency,
        detectable_area=_detectable_area(spectrum, cal, fits),
    )


# ---------------------------------------------------------------------- presentation
def candidate_table(result: IdentificationResult) -> Table:
    headers = ["Nuclide", "Class", "Score", "Peaks matched", "Lines missing"]
    headers += ["Shared peaks", "Intensity test", "Evaluation", "Why"]
    rows = []
    for c in result.candidates:
        shared = sum(1 for m in c.matches if m.shared_with)
        test = (
            "not possible"
            if c.intensity is None
            else f"χ² {c.intensity.chi2:.1f}/{c.intensity.ndf}"
            + ("" if c.intensity.consistent else " (inconsistent)")
        )
        rows.append(
            [
                c.nuclide,
                c.confidence.value,
                f"{c.score:.2f}",
                str(len(c.matched_peaks)),
                str(len(c.missing)),
                str(shared),
                test,
                f"{c.evaluation} ({c.dataset_id})",
                "; ".join(c.reasons),
            ]
        )
    return headers, rows


def evidence_rows(spectrum: Spectrum, result: IdentificationResult, c: Candidate) -> Rows:
    s = result.settings
    lo, hi = result.energy_range_kev
    rows: Rows = [
        ("Status", result_label(spectrum)),
        ("Candidate", f"{c.nuclide} — {c.confidence.value}"),
        ("Reference data", f"{c.evaluation}, dataset {c.dataset_id}"),
        (
            "Matching",
            f"|ΔE| ≤ {s.k:g}·√(u_peak² + u_line² + u_cal²"
            + (f" + {s.extra_energy_unc_kev:g}²" if s.extra_energy_unc_kev else "")
            + f"); lines of emission probability ≥ {s.min_emission_probability:g} between "
            f"{lo:.1f} and {hi:.1f} keV",
        ),
        (
            "Score",
            f"{c.score:.3f} = line fraction "
            f"{'n/a' if math.isnan(c.line_fraction) else format(c.line_fraction, '.3f')}"
            f" × uniqueness {c.uniqueness:.3f}"
            + (
                " × 0.5 (intensity test failed)"
                if c.intensity and not c.intensity.consistent
                else ""
            ),
        ),
    ]
    rows.extend(("Reason", r) for r in c.reasons)
    for m in c.matches:
        peak = result.peaks[m.peak_index]
        text = (
            f"peak at {peak.energy_kev:.3f} keV ({peak.label}); Δ = {m.delta_kev:+.3f} keV "
            f"= {m.z:+.2f} σ"
        )
        if m.shared_with:
            text += "; peak shared with " + ", ".join(m.shared_with)
        rows.append((f"Matched {m.line.energy_kev:.2f} keV", text))
    for u in c.unmatched:
        rows.append(
            (
                f"{'MISSING' if u.expected else 'Not seen'} {u.line.energy_kev:.2f} keV",
                u.reason,
            )
        )
    if c.intensity is not None:
        rows.append(
            (
                "Intensity test",
                f"χ² = {c.intensity.chi2:.2f} for {c.intensity.ndf} degrees of freedom, "
                f"probability {c.intensity.p_value:.3g}",
            )
        )
    rows.extend(("Caution", w) for w in (*c.warnings, *result.warnings))
    return rows


def library_table(library: ReferenceLibrary) -> Table:
    headers = ["Nuclide", "Evaluation", "Dataset", "Lines", "Intensities"]
    headers += ["Retrieved", "Reuse", "Citation"]
    rows = [
        [
            r.nuclide,
            r.provenance.evaluation,
            r.provenance.dataset_id,
            str(len(r.lines)),
            "relative (not normalised)" if r.has_relative_lines else "photons per decay",
            r.provenance.retrieval_date.isoformat(),
            r.provenance.reuse_status.value,
            r.provenance.citation,
        ]
        for r in library.records
    ]
    return headers, rows


def sources_table() -> Table:
    headers = ["Evaluation", "Role", "Maintainer", "Reuse terms", "Import"]
    return headers, [
        [s.evaluation, s.role, s.maintainer, s.terms, s.provider] for s in KNOWN_SOURCES
    ]


# ---------------------------------------------------------------------- export
def identification_csv_text(spectrum: Spectrum, result: IdentificationResult) -> str:
    s = result.settings
    lo, hi = result.energy_range_kev
    lines = [
        "# NSW nuclide candidates",
        f"# software: nsw {nsw.__version__}",
        f"# status: {result_label(spectrum)}",
        f"# spectrum: {spectrum_name(spectrum)}",
        f"# data sha256: {spectrum.data_sha256}",
        f"# matching: |dE| <= k*sqrt(u_peak^2+u_line^2+u_cal^2+u_extra^2), k = {s.k!r}, "
        f"u_extra = {s.extra_energy_unc_kev!r} keV",
        f"# lines considered: emission probability >= {s.min_emission_probability!r}, "
        f"{lo!r} to {hi!r} keV",
        f"# efficiency curve used: {'yes' if result.with_efficiency else 'no'}",
    ]
    lines.extend(f"# caution: {w}" for w in result.warnings)
    lines.extend(f"# not evaluated: {n}: {why}" for n, why in result.not_evaluated)
    lines.append(
        "nuclide,class,score,evaluation,dataset,line_keV,status,peak_keV,delta_keV,"
        "delta_sigma,shared_with,note"
    )
    for c in result.candidates:
        head = [c.nuclide, c.confidence.value, repr(float(c.score)), c.evaluation, c.dataset_id]
        for m in c.matches:
            peak = result.peaks[m.peak_index]
            cells = [
                repr(m.line.energy_kev),
                "matched",
                repr(peak.energy_kev),
                repr(float(m.delta_kev)),
                repr(float(m.z)),
                " ".join(m.shared_with),
                "",
            ]
            lines.append(",".join(head + cells))
        for u in c.unmatched:
            status = "missing" if u.expected else "not seen"
            cells = [repr(u.line.energy_kev), status, "", "", "", "", u.reason.replace(",", ";")]
            lines.append(",".join(head + cells))
    return "\n".join(lines) + "\n"


def export_identification_csv(
    spectrum: Spectrum,
    result: IdentificationResult,
    path: Path,
    protected_roots: Iterable[Path] = (),
    overwrite: bool = False,
) -> Path:
    target = ensure_output_allowed(path, protected_roots, overwrite)
    try:
        with target.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as fh:
            fh.write(identification_csv_text(spectrum, result))
    except OSError as exc:
        raise ExportError(f"could not write {target}: {exc}") from exc
    return target
