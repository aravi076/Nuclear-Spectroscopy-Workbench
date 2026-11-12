"""Use case: peak search and peak fitting, with readable tables and CSV export."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import nsw
from nsw.analysis.fitting import (
    BackgroundShape,
    CandidateFit,
    FitMethod,
    FitResult,
    FitSettings,
    MultipletEvidence,
    fit_peaks,
    fit_peaks_joint,
    fit_region_for,
    multiplet_evidence,
)
from nsw.analysis.peaks import (
    PeakCandidate,
    PeakSearchResult,
    PeakSearchSettings,
    estimate_fwhm_channels,
    find_peak_candidates,
)
from nsw.core.calibration import CalibrationStatus
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum
from nsw.services.export import (
    CALIBRATION_WARNING,
    SYNTHETIC_NOTICE,
    ExportError,
    ensure_output_allowed,
)

__all__ = [
    "BackgroundShape",
    "CandidateFit",
    "FitMethod",
    "FitResult",
    "FitSettings",
    "MultipletEvidence",
    "PeakCandidate",
    "PeakSearchResult",
    "PeakSearchSettings",
    "ProcessingError",
    "candidate_table",
    "default_scales",
    "evidence",
    "evidence_rows",
    "export_peaks_csv",
    "fit",
    "fit_all",
    "fit_joint",
    "fit_summary_rows",
    "group_region",
    "peak_table",
    "peaks_csv_text",
    "result_status",
    "search",
]

Rows = list[tuple[str, str]]
Table = tuple[list[str], list[list[str]]]
ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]
FALLBACK_SCALES = (4.0, 8.0)
HALF_WIDTH_FWHM = 3.0


def spectrum_name(s: Spectrum) -> str:
    return s.source.filename if s.source else (s.synthetic_id or "derived spectrum")


def result_status(s: Spectrum) -> str:
    """How results from this spectrum must be labelled."""
    if s.is_synthetic:
        return "SYNTHETIC — software verification only, not a measurement"
    if s.calibration_status is not CalibrationStatus.VERIFIED:
        return "EXPERIMENTAL, PROVISIONAL — channel units; energy calibration not verified"
    return "EXPERIMENTAL"


def default_scales(spectrum: Spectrum) -> tuple[float, ...]:
    """Detection scales from the strongest peak (w, 2w), or a generic pair if none stands out."""
    estimate = estimate_fwhm_channels(spectrum)
    if estimate is None:
        return FALLBACK_SCALES
    w = max(2.0, round(estimate, 1))
    return (w, round(2.0 * w, 1))


def search(spectrum: Spectrum, settings: PeakSearchSettings) -> PeakSearchResult:
    return find_peak_candidates(spectrum, settings)


def _num(value: float | None, fmt: str) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return format(value, fmt)


def candidate_table(result: PeakSearchResult) -> Table:
    with_energy = any(c.energy_kev is not None for c in result.candidates)
    headers = ["#", "Channel", "± (stat.)", "Significance", "FWHM est. (ch)"]
    headers += ["Area est.", "Height est.", "Scale (ch)", "Fit group"]
    if with_energy:
        headers.insert(3, "Energy (keV)")
    rows: list[list[str]] = []
    for i, c in enumerate(result.candidates, start=1):
        row = [
            str(i),
            f"{c.channel:.2f}",
            _num(c.channel_uncertainty, ".2f"),
            f"{c.significance:.1f}",
            _num(c.fwhm_estimate, ".2f"),
            f"{c.area_estimate:.4g}",
            f"{c.prominence:.4g}",
            f"{c.scale_fwhm:g}",
            str(c.group + 1),
        ]
        if with_energy:
            row.insert(3, _num(c.energy_kev, ".2f"))
        rows.append(row)
    return headers, rows


def group_region(spectrum: Spectrum, group: Sequence[PeakCandidate]) -> tuple[int, int]:
    return fit_region_for(
        spectrum,
        [c.channel for c in group],
        max(c.fwhm_guess for c in group),
        HALF_WIDTH_FWHM,
    )


def fit(
    spectrum: Spectrum,
    first_channel: int,
    last_channel: int,
    centroids: Sequence[float],
    fwhm_channels: float | Sequence[float],
    settings: FitSettings,
) -> FitResult:
    return fit_peaks(spectrum, first_channel, last_channel, centroids, fwhm_channels, settings)


def fit_joint(
    gross: Spectrum,
    background: Spectrum,
    first_channel: int,
    last_channel: int,
    centroids: Sequence[float],
    fwhm_channels: float | Sequence[float],
    settings: FitSettings,
) -> FitResult:
    return fit_peaks_joint(
        gross, background, first_channel, last_channel, centroids, fwhm_channels, settings
    )


def evidence(
    spectrum: Spectrum,
    first_channel: int,
    last_channel: int,
    centroids: Sequence[float],
    fwhm_channels: float | Sequence[float],
    settings: FitSettings,
) -> MultipletEvidence:
    return multiplet_evidence(
        spectrum, first_channel, last_channel, centroids, fwhm_channels, settings
    )


def fit_all(
    spectrum: Spectrum,
    found: PeakSearchResult,
    settings: FitSettings,
    progress: ProgressCallback | None = None,
    cancelled: CancelCheck | None = None,
) -> tuple[CandidateFit, ...]:
    """Fit every overlap group of a search; stops early (returning what is done) if cancelled."""
    if found.spectrum_sha256 != spectrum.data_sha256:
        raise ProcessingError("the peak search belongs to a different spectrum")
    groups = found.groups()
    out: list[CandidateFit] = []
    for i, group in enumerate(groups):
        if cancelled is not None and cancelled():
            break
        if progress is not None:
            progress(i, len(groups), f"fitting group {i + 1} of {len(groups)}")
        try:
            first, last = group_region(spectrum, group)
            result = fit_peaks(
                spectrum,
                first,
                last,
                [c.channel for c in group],
                [c.fwhm_guess for c in group],
                settings,
            )
        except ProcessingError as exc:
            out.append(CandidateFit(group[0].group, None, str(exc)))
        else:
            out.append(CandidateFit(group[0].group, result, None))
    if progress is not None:
        progress(len(out), len(groups), "done")
    return tuple(out)


def _quality(r: FitResult) -> str:
    if not r.converged:
        return "NOT CONVERGED"
    if r.flags:
        return "FLAGGED"
    if r.poor_fit:
        return "POOR FIT"
    return "ok"


def _pm(value: float, unc: float, fmt: str) -> tuple[str, str]:
    return format(value, fmt), _num(unc, fmt)


PEAK_HEADERS = [
    "Region",
    "Centroid (ch)",
    "±",
    "Area (counts)",
    "±",
    "FWHM (ch)",
    "±",
    "Height",
    "Area/σ",
    "χ²/ndf",
    "p",
    "Method",
    "Quality",
]


def peak_table(results: Iterable[FitResult]) -> Table:
    """One row per fitted component. An energy column appears only with a verified
    calibration."""
    results = list(results)
    with_energy = any(p.energy_kev is not None for r in results for p in r.peaks)
    headers = list(PEAK_HEADERS)
    if with_energy:
        headers.insert(3, "Energy (keV)")
    rows: list[list[str]] = []
    for r in results:
        for p in sorted(r.peaks, key=lambda q: q.centroid):
            centroid = _pm(p.centroid, p.centroid_unc, ".3f")
            if r.settings.fixed_centroids:  # held at a supplied value: no uncertainty from this fit
                centroid = (centroid[0], "fixed")
            fwhm = _pm(p.fwhm, p.fwhm_unc, ".3f")
            if r.settings.fixed_width:
                fwhm = (fwhm[0], "fixed")
            row = [
                f"{r.first_channel}–{r.last_channel}",
                *centroid,
                *_pm(p.area, p.area_unc, ".1f"),
                *fwhm,
                f"{p.amplitude:.4g}",
                _num(p.significance, ".1f"),
                f"{r.pearson_chi2:.1f}/{r.ndf}",
                _num(r.p_value, ".3g"),
                r.method,
                _quality(r),
            ]
            if with_energy:
                row.insert(3, _num(p.energy_kev, ".3f"))
            rows.append(row)
    return headers, rows


def fit_summary_rows(spectrum: Spectrum, r: FitResult) -> Rows:
    rows: Rows = [
        ("Result status", result_status(spectrum)),
        ("Spectrum", spectrum_name(spectrum)),
        ("Fit region", f"channels {r.first_channel}–{r.last_channel} ({r.channels.size} channels)"),
        ("Estimator", f"{r.method}; statistic: {r.statistic_name} = {r.statistic:.2f}"),
        (
            "Model",
            f"{r.n_peaks} Gaussian(s), integrated over each channel; continuum: "
            f"{r.settings.background.value}"
            + ("; step" if r.settings.step else "")
            + ("; common width" if r.settings.shared_width and r.n_peaks > 1 else ""),
        ),
        ("Outcome", _quality(r) + (": " + "; ".join(r.flags) if r.flags else "")),
        ("Pearson χ² / ndf", f"{r.pearson_chi2:.2f} / {r.ndf}  (reduced {r.reduced_chi2:.2f})"),
        ("Goodness-of-fit probability", f"{_num(r.p_value, '.3g')}  — {r.gof_method}"),
        (
            "Runs test on residual signs",
            f"z = {r.runs_z:+.2f}  (|z| > 3 indicates structure in the residuals)"
            if r.runs_z is not None
            else "not defined",
        ),
        ("Iterations", str(r.iterations)),
    ]
    for name, value, unc in r.background:
        rows.append((f"Continuum: {name}", f"{value:.4g} ± {_num(unc, '.2g')} counts/channel"))
    if r.step_fraction is not None:
        rows.append(
            (
                "Step fraction",
                f"{r.step_fraction[0]:.4g} ± {_num(r.step_fraction[1], '.2g')}  "
                "(step height / peak height)",
            )
        )
    rows.append(("Uncertainties", "1 standard deviation, from the inverse Fisher information"))
    rows.extend(("Caution", w) for w in r.warnings)
    return rows


def evidence_rows(ev: MultipletEvidence) -> Rows:
    return [
        ("Multiplet assessment", ev.statement),
        ("Components compared", f"{ev.n_components} versus {ev.n_components - 1}"),
        ("ΔAIC / ΔBIC", f"{_num(ev.delta_aic, '.1f')} / {_num(ev.delta_bic, '.1f')}"),
        (
            "Separation of neighbours",
            ", ".join(f"{s:.2f} FWHM" for s in ev.separations_fwhm),
        ),
        (
            "Correlation of neighbouring areas",
            ", ".join(_num(c, "+.2f") for c in ev.area_correlations),
        ),
    ]


def peaks_csv_text(spectrum: Spectrum, results: Sequence[FitResult]) -> str:
    """CSV of fitted peaks with a commented provenance header."""
    s = spectrum
    head = [
        "# NSW peak fit results",
        f"# software: nsw {nsw.__version__}",
        f"# status: {result_status(s)}",
        f"# spectrum: {spectrum_name(s)}",
        f"# data sha256: {s.data_sha256}",
        f"# origin: {s.origin.value}; kind: {s.kind.value}",
        f"# live time (s): {s.live_time_s if s.live_time_s is not None else 'not recorded'}",
    ]
    if s.is_synthetic:
        head.append(f"# {SYNTHETIC_NOTICE}")
    verified = s.calibration_status is CalibrationStatus.VERIFIED
    if not verified:
        head.append(f"# {CALIBRATION_WARNING}")
        head.append("# positions and widths are in channels (1-based)")
    head.append("# model: Gaussian integrated over each channel; FWHM = 2.35482 sigma")
    head.append("# uncertainties: 1 standard deviation from the inverse Fisher information")
    head.append("# quality: ok | POOR FIT (model inadequate) | FLAGGED | NOT CONVERGED")
    columns = ["first_channel", "last_channel", "centroid_channel", "centroid_unc"]
    if verified:
        columns.append("energy_keV")
    columns += ["area_counts", "area_unc", "fwhm_channels", "fwhm_unc", "height", "height_unc"]
    columns += ["pearson_chi2", "ndf", "p_value", "method", "quality", "flags"]
    lines = [*head, ",".join(columns)]
    for r in results:
        for p in sorted(r.peaks, key=lambda q: q.centroid):
            cells = [
                str(r.first_channel),
                str(r.last_channel),
                repr(p.centroid),
                repr(p.centroid_unc),
            ]
            if verified:
                cells.append(repr(p.energy_kev))
            cells += [repr(p.area), repr(p.area_unc), repr(p.fwhm), repr(p.fwhm_unc)]
            cells += [repr(p.amplitude), repr(p.amplitude_unc), repr(r.pearson_chi2), str(r.ndf)]
            cells += [repr(r.p_value), r.method, _quality(r), "; ".join(r.flags).replace(",", " ")]
            lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def export_peaks_csv(
    spectrum: Spectrum,
    results: Sequence[FitResult],
    path: Path,
    protected_roots: Iterable[Path] = (),
    overwrite: bool = False,
) -> Path:
    if not results:
        raise ExportError("there are no fit results to export")
    target = ensure_output_allowed(path, protected_roots, overwrite)
    try:
        with target.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as fh:
            fh.write(peaks_csv_text(spectrum, results))
    except OSError as exc:
        raise ExportError(f"could not write {target}: {exc}") from exc
    return target
