"""Use case: energy calibration from fitted peaks, with tables, history and export.

Policy for experimental data
----------------------------
A calibration of *experimental* spectra is always stored as UNVERIFIED ("provisional")
while ``EXPERIMENTAL_VERIFICATION_ENABLED`` is False. The project owner has to confirm
which reference lines belong to which measured source and name a traceable source for
their energies before a calibration of the laboratory data may be called verified
(project rule). Spectra therefore keep their channel
axis. Synthetic spectra can be verified when the fit has redundancy and is acceptable.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

import nsw
from nsw.analysis.energy_calibration import (
    CalibrationFit,
    OrderComparison,
    StabilityResult,
    compare_orders,
    fit_energy_calibration,
    position_stability,
)
from nsw.analysis.fitting import FitResult
from nsw.core.calibration import CalibrationPoint, CalibrationStatus, EnergyCalibration
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import MeasurementStatus, Spectrum
from nsw.services.export import ExportError, ensure_output_allowed
from nsw.services.peaks import spectrum_name

__all__ = [
    "EXPERIMENTAL_VERIFICATION_ENABLED",
    "CalibrationFit",
    "CalibrationHistoryItem",
    "CalibrationPoint",
    "EnergyCalibration",
    "OrderComparison",
    "PeakRow",
    "ProcessingError",
    "StabilityResult",
    "apply_calibration",
    "calibrate",
    "calibration_csv_text",
    "curve",
    "export_calibration_csv",
    "fit_rows",
    "history_table",
    "make_point",
    "make_record",
    "order_test",
    "peak_rows",
    "point_table",
    "stability_of_line",
    "status_text",
    "verification",
]

EXPERIMENTAL_VERIFICATION_ENABLED = False
Rows = list[tuple[str, str]]
Table = tuple[list[str], list[list[str]]]
PROVISIONAL_REASON = (
    "calibrations of experimental spectra stay provisional until the laboratory confirms "
    "the line assignments and a traceable source of the reference energies"
)


@dataclass(frozen=True, slots=True)
class PeakRow:
    """One fitted peak offered as a calibration point."""

    centroid: float
    centroid_unc: float
    area: float
    fwhm: float
    fwhm_unc: float  # 0 when the width was held fixed in the fit
    quality: str  # "ok" or "POOR FIT"
    region: tuple[int, int]
    area_unc: float = math.nan


@dataclass(frozen=True)
class CalibrationHistoryItem:
    created: datetime
    spectrum: str
    synthetic: bool
    record: EnergyCalibration
    fit: CalibrationFit


def peak_rows(fits: Iterable[FitResult]) -> list[PeakRow]:
    """Peaks usable as calibration points: converged, unflagged fits with a position
    uncertainty and a significant area. Peaks of poor fits are offered but marked."""
    rows: list[PeakRow] = []
    for r in fits:
        if not r.ok:
            continue
        for p in r.peaks:
            if not math.isfinite(p.centroid_unc) or p.centroid_unc <= 0.0:
                continue
            if not (math.isfinite(p.significance) and p.significance >= 3.0):
                continue
            rows.append(
                PeakRow(
                    centroid=p.centroid,
                    centroid_unc=p.centroid_unc,
                    area=p.area,
                    fwhm=p.fwhm,
                    fwhm_unc=p.fwhm_unc,
                    quality="POOR FIT" if r.poor_fit else "ok",
                    region=(r.first_channel, r.last_channel),
                    area_unc=p.area_unc,
                )
            )
    rows.sort(key=lambda row: row.centroid)
    return rows


def make_point(
    row: PeakRow, energy_kev: float, energy_unc_kev: float, source: str, label: str = ""
) -> CalibrationPoint:
    """Reference point from a fitted peak and an analyst-supplied reference energy."""
    if not source.strip():
        raise ProcessingError(
            f"the reference energy for the peak at channel {row.centroid:.2f} has no source; "
            "every reference value needs its origin recorded"
        )
    if not math.isfinite(energy_kev) or energy_kev <= 0.0:
        raise ProcessingError("a reference energy must be a positive number (keV)")
    if not math.isfinite(energy_unc_kev) or energy_unc_kev < 0.0:
        raise ProcessingError("a reference-energy uncertainty must be >= 0 (keV)")
    return CalibrationPoint(
        channel=row.centroid,
        channel_uncertainty=row.centroid_unc,
        reference_value=energy_kev,
        reference_uncertainty=energy_unc_kev,
        reference_source=source.strip(),
        label=label.strip(),
    )


def calibrate(
    points: Sequence[CalibrationPoint], order: int = 1, inflate: bool = False
) -> CalibrationFit:
    return fit_energy_calibration(points, order, inflate=inflate)


def order_test(points: Sequence[CalibrationPoint], order: int = 1) -> OrderComparison:
    return compare_orders(points, order)


def verification(spectrum: Spectrum, fit: CalibrationFit) -> tuple[bool, str]:
    """May this fit be stored as VERIFIED for this spectrum, and if not, why."""
    if not fit.verifiable:
        return False, fit.why_not_verifiable()
    if not spectrum.is_synthetic and not EXPERIMENTAL_VERIFICATION_ENABLED:
        return False, PROVISIONAL_REASON
    return True, ""


def status_text(spectrum: Spectrum, fit: CalibrationFit) -> str:
    ok, reason = verification(spectrum, fit)
    if spectrum.is_synthetic:
        head = "SYNTHETIC — " + ("verified against synthetic reference values" if ok else "")
        return head + ("" if ok else f"not verified: {reason}")
    return f"EXPERIMENTAL, PROVISIONAL — not verified: {reason}"


def make_record(
    spectrum: Spectrum, fit: CalibrationFit, created: datetime, note: str = ""
) -> EnergyCalibration:
    ok, reason = verification(spectrum, fit)
    return fit.to_record(
        verified=ok,
        detector_id=spectrum.detector_id,
        created=created,
        note=(note if ok else f"{note} Not verified: {reason}.").strip(),
        synthetic=spectrum.is_synthetic,
    )


def apply_calibration(spectrum: Spectrum, record: EnergyCalibration) -> Spectrum:
    """The same spectrum with ``record`` attached. Energies become available only if the
    record is verified; an unverified record leaves the spectrum in channel units."""
    if record.detector_id and spectrum.detector_id and record.detector_id != spectrum.detector_id:
        raise ProcessingError(
            f"calibration belongs to detector {record.detector_id!r}, "
            f"the spectrum to {spectrum.detector_id!r}"
        )
    return spectrum.evolve(energy_calibration=record)


# ---------------------------------------------------------------------- presentation
def _g(value: float, fmt: str = ".6g") -> str:
    return format(value, fmt) if math.isfinite(value) else "—"


def fit_rows(spectrum: Spectrum, fit: CalibrationFit) -> Rows:
    lo, hi = fit.channel_range
    e_lo, e_hi = fit.energy_range_kev
    names = ("offset c0 (keV)", "gain c1 (keV/channel)", "c2 (keV/channel²)", "c3 (keV/channel³)")
    rows: Rows = [
        ("Status", status_text(spectrum, fit)),
        ("Spectrum", spectrum_name(spectrum)),
        ("Model", f"E(ch) = Σ c_j ch^j, order {fit.order}; {fit.n_points} reference points"),
    ]
    for name, c, u in zip(names, fit.coefficients, fit.coefficient_uncertainties, strict=False):
        rows.append((name, f"{c:.9g} ± {u:.3g}"))
    rows += [
        (
            "χ² / ndf",
            f"{fit.chi2:.3f} / {fit.ndf}"
            + (f"  (reduced {fit.reduced_chi2:.2f})" if fit.ndf else ""),
        ),
        ("Probability", _g(fit.p_value, ".3g") if fit.ndf else "not defined (no redundancy)"),
        (
            "Valid range",
            f"channels {lo:.1f}–{hi:.1f}  ({e_lo:.2f}–{e_hi:.2f} keV); outside this range "
            "energies are extrapolated",
        ),
        (
            "Largest residual",
            f"{float(np.max(np.abs(fit.residuals_kev))):.4g} keV "
            f"({float(np.max(np.abs(fit.normalised_residuals))):.2f} σ)",
        ),
        (
            "Uncertainties",
            "1 standard deviation; channel and reference-energy uncertainties combined "
            "(effective variance)"
            + ("; covariance multiplied by χ²/ndf" if fit.inflated else "; not rescaled by χ²"),
        ),
    ]
    rows.extend(("Caution", w) for w in fit.warnings)
    return rows


def point_table(fit: CalibrationFit) -> Table:
    headers = ["Label", "Channel", "±", "Reference (keV)", "±", "Fitted (keV)"]
    headers += ["Residual (keV)", "Residual / σ", "Source of reference"]
    fitted, _, _ = fit.energy([p.channel for p in fit.points])
    rows = [
        [
            p.label,
            f"{p.channel:.3f}",
            f"{p.channel_uncertainty:.3f}",
            f"{p.reference_value:.4f}",
            f"{p.reference_uncertainty:.4f}",
            f"{e:.4f}",
            f"{res:+.4f}",
            f"{norm:+.2f}",
            p.reference_source,
        ]
        for p, e, res, norm in zip(
            fit.points, fitted, fit.residuals_kev, fit.normalised_residuals, strict=True
        )
    ]
    return headers, rows


def curve(
    fit: CalibrationFit, n: int = 200, margin: float = 0.05
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Channels, energies and 1-sigma band of the fitted curve, slightly beyond the range."""
    lo, hi = fit.channel_range
    pad = margin * (hi - lo)
    ch = np.linspace(max(1.0, lo - pad), hi + pad, n)
    e, u, _ = fit.energy(ch)
    return ch, e, u


def history_table(history: Sequence[CalibrationHistoryItem]) -> Table:
    headers = ["Created", "Spectrum", "Data", "Order", "Points", "Gain (keV/ch)", "±"]
    headers += ["χ²/ndf", "Range (channels)", "Status"]
    rows = []
    for item in history:
        f = item.fit
        lo, hi = f.channel_range
        rows.append(
            [
                item.created.strftime("%Y-%m-%d %H:%M:%S"),
                item.spectrum,
                "synthetic" if item.synthetic else "experimental",
                str(f.order),
                str(f.n_points),
                f"{f.coefficients[1]:.8g}",
                f"{f.coefficient_uncertainties[1]:.2g}",
                f"{f.chi2:.2f}/{f.ndf}",
                f"{lo:.0f}–{hi:.0f}",
                item.record.status.value
                if item.record.status is CalibrationStatus.VERIFIED
                else "unverified (provisional)",
            ]
        )
    return headers, rows


# ---------------------------------------------------------------------- stability
def stability_of_line(
    spectra_and_fits: Iterable[tuple[Spectrum, Sequence[FitResult]]],
    channel: float,
    tolerance: float,
) -> tuple[StabilityResult, Table]:
    """Constancy of one peak position across independent spectra.

    Uses the fitted peak nearest to ``channel`` (within ``tolerance`` channels) of every
    independent, non-synthetic-mixed spectrum that has an acceptable fit there.
    Intermediate saves are excluded: they are not independent measurements.
    """
    positions: list[float] = []
    uncertainties: list[float] = []
    table_rows: list[list[str]] = []
    origins: set[bool] = set()
    for spectrum, fits in spectra_and_fits:
        if spectrum.measurement_status is MeasurementStatus.INTERMEDIATE_SAVE:
            continue
        candidates = [
            row
            for row in peak_rows(fits)
            if row.quality == "ok" and abs(row.centroid - channel) <= tolerance
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda row: abs(row.centroid - channel))
        positions.append(best.centroid)
        uncertainties.append(best.centroid_unc)
        origins.add(spectrum.is_synthetic)
        start = spectrum.start_time.strftime("%Y-%m-%d %H:%M") if spectrum.start_time else "—"
        table_rows.append(
            [spectrum_name(spectrum), start, f"{best.centroid:.3f}", f"{best.centroid_unc:.3f}"]
        )
    if len(origins) > 1:
        raise ProcessingError("experimental and synthetic spectra cannot be compared")
    if len(positions) < 2:
        raise ProcessingError(
            f"fewer than two independent spectra have an acceptable fitted peak within "
            f"{tolerance:g} channels of channel {channel:g}"
        )
    result = position_stability(positions, uncertainties)
    headers = ["Spectrum", "Start", "Position (ch)", "±"]
    return result, (headers, table_rows)


# ---------------------------------------------------------------------- export
def calibration_csv_text(spectrum: Spectrum, fit: CalibrationFit) -> str:
    head = [
        "# NSW energy calibration",
        f"# software: nsw {nsw.__version__}",
        f"# status: {status_text(spectrum, fit)}",
        f"# spectrum: {spectrum_name(spectrum)}",
        f"# data sha256: {spectrum.data_sha256}",
        f"# detector: {spectrum.detector_id or 'not recorded'}",
        f"# model: E(ch) = sum c_j ch^j (keV, 1-based channels), order {fit.order}",
    ]
    for j, (c, u) in enumerate(zip(fit.coefficients, fit.coefficient_uncertainties, strict=True)):
        head.append(f"# c{j} = {float(c)!r} +- {float(u)!r}")
    for i, row in enumerate(fit.covariance):
        head.append(f"# covariance row {i}: " + " ".join(repr(float(v)) for v in row))
    lo, hi = fit.channel_range
    head.append(f"# chi2 = {fit.chi2!r}, ndf = {fit.ndf}, p = {fit.p_value!r}")
    head.append(f"# valid channel range: {lo!r} .. {hi!r} (extrapolation outside)")
    head.append(
        "# covariance multiplied by chi2/ndf"
        if fit.inflated
        else "# uncertainties not rescaled by chi2"
    )
    head.extend(f"# caution: {w}" for w in fit.warnings)
    head.append(
        "label,channel,channel_unc,reference_keV,reference_unc_keV,fitted_keV,"
        "residual_keV,normalised_residual,reference_source"
    )
    fitted, _, _ = fit.energy([p.channel for p in fit.points])
    lines = list(head)
    for p, e, res, norm in zip(
        fit.points, fitted, fit.residuals_kev, fit.normalised_residuals, strict=True
    ):
        cells = [
            p.label.replace(",", " "),
            repr(p.channel),
            repr(p.channel_uncertainty),
            repr(p.reference_value),
            repr(p.reference_uncertainty),
            repr(float(e)),
            repr(float(res)),
            repr(float(norm)),
            p.reference_source.replace(",", " "),
        ]
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def export_calibration_csv(
    spectrum: Spectrum,
    fit: CalibrationFit,
    path: Path,
    protected_roots: Iterable[Path] = (),
    overwrite: bool = False,
) -> Path:
    target = ensure_output_allowed(path, protected_roots, overwrite)
    try:
        with target.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as fh:
            fh.write(calibration_csv_text(spectrum, fit))
    except OSError as exc:
        raise ExportError(f"could not write {target}: {exc}") from exc
    return target
