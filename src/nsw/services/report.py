"""Use case: a reproducible analysis report (PDF) for one spectrum, and figure export.

The report states everything needed to reconstruct the results: spectrum identity and
data hash, detector and acquisition metadata, calibration status, background handling,
peak search and fit settings and results, efficiency and activity when available,
nuclide candidates with evidence and the provenance of the reference data, equations
and conventions, uncertainties, warnings and limitations, software versions. Results
are labelled synthetic, experimental or provisional exactly as in the application.

No report is written into an experimental dataset folder.
"""

from __future__ import annotations

import platform
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import scipy

import nsw
from nsw.analysis.activity import ActivityResult
from nsw.analysis.efficiency import EfficiencyCurve
from nsw.analysis.fitting import FitResult
from nsw.analysis.identification import IdentificationResult
from nsw.analysis.peaks import PeakSearchResult
from nsw.core.calibration import CalibrationStatus
from nsw.core.nuclear_data import ReferenceLibrary
from nsw.core.spectrum import Spectrum
from nsw.reporting.document import Overlay, Report, Section, SpectrumFigure, write_figure, write_pdf
from nsw.services import describe
from nsw.services import efficiency as efficiency_service
from nsw.services import identification as identification_service
from nsw.services.export import ExportError, ensure_output_allowed
from nsw.services.peaks import peak_table, result_status, spectrum_name

__all__ = ["Report", "build_report", "export_figure", "export_report", "spectrum_figure"]

METHODS = """\
Channels are 1-based. Count spectra have Poisson variance; derived spectra carry an explicit variance and may be negative.
Background subtraction: net = gross − α·background, α = t_live(gross)/t_live(background); variance = gross + α²·background.
Peak fit: channel-integrated Gaussians on a constant or linear continuum (optional step). Poisson spectra by maximum likelihood (deviance), derived spectra by weighted least squares. Covariance from the expected Fisher information, never rescaled by χ². FWHM = 2√(2 ln 2)·σ.
Energy calibration: E(ch) = Σ c_j ch^j by effective-variance regression; spectra stay in channel units unless the calibration is verified.
Efficiency: ε = N / (P_γ · A₀ · D · f · t_live), D = exp(−λΔt), f = (1 − e^(−λ t_real))/(λ t_real); ln ε = Σ a_k (ln E/E₀)^k by generalised least squares.
Activity: A = N / (ε · P_γ · t_live · D · f), combined over lines by generalised least squares with the correlated efficiency uncertainty.
Nuclide candidates: |E_peak − E_line| ≤ k·√(u_peak² + u_line² + u_cal² + u_extra²); classes not supported / possible / probable / consistent; never an identification.
Uncertainties are one standard uncertainty unless stated. Full description: docs/science/scientific_methodology.md."""

LIMITATIONS = """\
Peak model without low-energy tail: strong HPGe peaks can be reported as poor fits.
Weak peaks fitted with free position and width are biased high; detection limits are not reported.
No correction for true coincidence summing, self-attenuation or geometry differences.
No gain-drift model: a calibration has no validity period in time.
Synthetic results show the behaviour of the software on simulated data; they are not a validation of any detector."""


def _banner(spectrum: Spectrum) -> tuple[str, str]:
    if spectrum.is_synthetic:
        return "SYNTHETIC DATA — software verification only, not a measurement", "synthetic"
    if spectrum.calibration_status is not CalibrationStatus.VERIFIED:
        return (
            "EXPERIMENTAL, PROVISIONAL — energy calibration not verified; channel units",
            "provisional",
        )
    return "EXPERIMENTAL", "experimental"


def spectrum_figure(spectrum: Spectrum, fits: Sequence[FitResult] = ()) -> SpectrumFigure:
    """Spectrum in channel units (energy only with a verified calibration), with the
    fitted models overlaid."""
    channels = spectrum.channels.astype(np.float64)
    verified = spectrum.calibration_status is CalibrationStatus.VERIFIED
    x = spectrum.energies() if verified else channels
    overlays: list[Overlay] = []
    for r in fits:
        rx = np.asarray(r.channels, dtype=np.float64)
        if verified and spectrum.energy_calibration is not None:
            rx = spectrum.energy_calibration.energy_at(rx)
        label = "fitted model" if not overlays else ""
        overlays.append(Overlay(rx, np.asarray(r.fitted, dtype=np.float64), label))
    return SpectrumFigure(
        x=x,
        y=np.asarray(spectrum.counts, dtype=np.float64),
        x_label="Energy (keV)" if verified else "Channel",
        y_label="Counts per channel" if spectrum.is_count_data else "Net counts per channel",
        log_y=spectrum.is_count_data,
        overlays=tuple(overlays),
        title=spectrum_name(spectrum),
    )


def build_report(
    spectrum: Spectrum,
    *,
    search: PeakSearchResult | None = None,
    fits: Sequence[FitResult] = (),
    efficiency: EfficiencyCurve | None = None,
    activity: ActivityResult | None = None,
    identification: IdentificationResult | None = None,
    library: ReferenceLibrary | None = None,
    background_note: str = "",
    created: datetime | None = None,
) -> Report:
    created = created or datetime.now(UTC)
    banner, kind = _banner(spectrum)
    sections: list[Section] = [
        Section("Measurement", describe.measurement_rows(spectrum)),
        Section("File and data identity", describe.file_rows(spectrum)),
        Section("Detector and acquisition", describe.detector_rows(spectrum)),
        Section("Energy calibration", describe.calibration_rows(spectrum)),
        Section(
            "Background handling",
            text=background_note
            or (
                "Derived spectrum; see the processing history."
                if not spectrum.is_count_data
                else "No background subtracted in this report (gross spectrum)."
            ),
        ),
        Section("Processing history", describe.history_rows(spectrum)),
    ]
    if search is not None:
        s = search.settings
        sections.append(
            Section(
                "Peak search",
                [
                    (
                        "Detection scales (FWHM, channels)",
                        ", ".join(f"{w:g}" for w in s.fwhm_channels),
                    ),
                    ("Significance threshold", f"{s.threshold:g}"),
                    ("Region", f"channels {search.first_channel}–{search.last_channel}"),
                    ("Candidates", str(len(search.candidates))),
                    *(("Caution", w) for w in search.warnings),
                ],
            )
        )
    if fits:
        settings = sorted({f"{r.method}, continuum {r.settings.background.value}"
                           + (", step" if r.settings.step else "")
                           + (", positions fixed" if r.settings.fixed_centroids else "")
                           + (", width fixed" if r.settings.fixed_width else "")
                           for r in fits})  # fmt: skip
        warnings = sorted({w for r in fits for w in r.warnings})
        sections.append(
            Section(
                "Peak fits",
                [("Fit settings", "; ".join(settings)), ("Status", result_status(spectrum))],
                table=peak_table(fits),
                text="\n".join(f"Caution: {w}" for w in warnings),
            )
        )
    if efficiency is not None:
        sections.append(
            Section("Efficiency calibration", efficiency_service.curve_rows(spectrum, efficiency))
        )
    if activity is not None:
        sections.append(Section("Activity", efficiency_service.activity_rows(spectrum, activity)))
    if identification is not None:
        st = identification.settings
        sections.append(
            Section(
                "Nuclide candidates",
                [
                    (
                        "Window",
                        f"k = {st.k:g}, extra energy uncertainty {st.extra_energy_unc_kev:g} keV",
                    ),
                    ("Energy range", "{:.1f}–{:.1f} keV".format(*identification.energy_range_kev)),
                    ("Efficiency curve used", "yes" if identification.with_efficiency else "no"),
                    *(("Caution", w) for w in identification.warnings),
                ],
                table=identification_service.candidate_table(identification),
            )
        )
        if library is not None:
            sections.append(
                Section(
                    "Reference data (provenance)",
                    table=identification_service.library_table(library),
                )
            )
    sections.append(Section("Methods and conventions", text=METHODS))
    sections.append(Section("Limitations", text=LIMITATIONS))
    sections.append(
        Section(
            "Software and reproducibility",
            [
                ("NSW", nsw.__version__),
                (
                    "Python / NumPy / SciPy",
                    f"{platform.python_version()} / {np.__version__} / {scipy.__version__}",
                ),
                ("Report created (UTC)", created.isoformat(timespec="seconds")),
                ("Spectrum data SHA-256", spectrum.data_sha256),
            ],
        )
    )
    return Report(
        title=f"NSW analysis report — {spectrum_name(spectrum)}",
        banner=banner,
        banner_kind=kind,
        sections=sections,
        figure=spectrum_figure(spectrum, fits),
        footer=f"nsw {nsw.__version__}; data {spectrum.data_sha256[:16]}…",
    )


def export_report(
    report: Report, path: Path, protected_roots: Iterable[Path] = (), overwrite: bool = False
) -> Path:
    target = ensure_output_allowed(path, protected_roots, overwrite)
    if target.suffix.lower() != ".pdf":
        raise ExportError("reports are written as PDF (.pdf)")
    try:
        return write_pdf(report, target)
    except OSError as exc:
        raise ExportError(f"could not write {target}: {exc}") from exc


def export_figure(
    spectrum: Spectrum,
    fits: Sequence[FitResult],
    path: Path,
    protected_roots: Iterable[Path] = (),
    overwrite: bool = False,
) -> Path:
    target = ensure_output_allowed(path, protected_roots, overwrite)
    if target.suffix.lower() not in (".svg", ".pdf", ".png"):
        raise ExportError("figures are written as SVG, PDF or PNG")
    banner, _ = _banner(spectrum)
    try:
        return write_figure(spectrum_figure(spectrum, fits), target, banner)
    except OSError as exc:
        raise ExportError(f"could not write {target}: {exc}") from exc
