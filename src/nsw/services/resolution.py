"""Use case: resolution analysis (FWHM versus channel or energy) from fitted peaks.

Experimental spectra have no verified energy calibration at present, so their
resolution is analysed in channel units and labelled provisional. With a verified
calibration (currently synthetic spectra only) widths are converted to keV with the
local gain; the gain uncertainty is not propagated (it is zero for synthetic truth and
negligible against the width uncertainty otherwise).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

import nsw
from nsw.analysis.fitting import FitResult
from nsw.analysis.resolution import (
    ResolutionFit,
    ResolutionVariable,
    WidthPoint,
    fit_resolution,
    fwhm_in_kev,
)
from nsw.core.calibration import CalibrationStatus
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum
from nsw.services.calibration import PeakRow, peak_rows
from nsw.services.export import ExportError, ensure_output_allowed
from nsw.services.peaks import spectrum_name

__all__ = [
    "PeakRow",
    "ProcessingError",
    "ResolutionFit",
    "ResolutionVariable",
    "WidthPoint",
    "analyse",
    "available_variables",
    "curve",
    "export_resolution_csv",
    "fit_rows",
    "point_table",
    "resolution_csv_text",
    "status_text",
    "usable_rows",
    "verification",
    "width_points",
]

Rows = list[tuple[str, str]]
Table = tuple[list[str], list[list[str]]]


def available_variables(spectrum: Spectrum) -> tuple[ResolutionVariable, ...]:
    if spectrum.calibration_status is CalibrationStatus.VERIFIED:
        return (ResolutionVariable.ENERGY_KEV, ResolutionVariable.CHANNEL)
    return (ResolutionVariable.CHANNEL,)


def usable_rows(fits: Iterable[FitResult]) -> list[PeakRow]:
    """Fitted peaks whose width may enter a resolution model: acceptable fits only (the
    width of a poorly described peak is not a resolution measurement) and only widths
    that were actually fitted (not held fixed)."""
    return [
        row
        for row in peak_rows(fits)
        if row.quality == "ok" and math.isfinite(row.fwhm_unc) and row.fwhm_unc > 0.0
    ]


def width_points(
    spectrum: Spectrum, rows: Sequence[PeakRow], variable: ResolutionVariable
) -> list[WidthPoint]:
    if variable is ResolutionVariable.CHANNEL:
        return [WidthPoint(r.centroid, r.fwhm, r.fwhm_unc, f"ch {r.centroid:.1f}") for r in rows]
    cal = spectrum.energy_calibration
    if cal is None or not cal.is_verified:
        raise ProcessingError(
            "widths in keV need a verified energy calibration; analyse this spectrum in "
            "channel units"
        )
    out = []
    for r in rows:
        energy = float(cal.energy_at(r.centroid))
        value, unc = fwhm_in_kev(r.fwhm, r.fwhm_unc, float(cal.gain_at(r.centroid)))
        out.append(WidthPoint(energy, value, unc, f"{energy:.1f} keV"))
    return out


def analyse(
    spectrum: Spectrum,
    rows: Sequence[PeakRow],
    variable: ResolutionVariable,
    quadratic: bool = False,
) -> ResolutionFit:
    return fit_resolution(width_points(spectrum, rows, variable), variable, quadratic=quadratic)


def verification(spectrum: Spectrum, fit: ResolutionFit) -> tuple[bool, str]:
    if fit.variable is not ResolutionVariable.ENERGY_KEV:
        return False, "channel units: tied to an unverified energy scale"
    if not fit.verifiable:
        return False, "needs a redundant point and a statistically acceptable fit"
    if not spectrum.is_synthetic:
        return False, "experimental resolution models stay provisional at this stage"
    return True, ""


def status_text(spectrum: Spectrum, fit: ResolutionFit) -> str:
    ok, reason = verification(spectrum, fit)
    if spectrum.is_synthetic:
        return "SYNTHETIC — " + ("verified against fitted synthetic peaks" if ok else reason)
    return f"EXPERIMENTAL, PROVISIONAL — {reason}"


def _g(value: float, fmt: str = ".5g") -> str:
    return format(value, fmt) if math.isfinite(value) else "—"


def fit_rows(spectrum: Spectrum, fit: ResolutionFit) -> Rows:
    unit = fit.unit
    x_name = "E (keV)" if fit.variable is ResolutionVariable.ENERGY_KEV else "channel"
    lo, hi = fit.x_range
    units = (f"{unit}²", f"{unit}² per {x_name.split()[0]}", "dimensionless")
    rows: Rows = [
        ("Status", status_text(spectrum, fit)),
        ("Spectrum", spectrum_name(spectrum)),
        ("Model", f"FWHM² = a + b·x + c·x²,  x = {x_name},  FWHM in {unit}"),
    ]
    for name, value, unc, fitted, requested, u in zip(
        ("a", "b", "c"),
        fit.coefficients,
        fit.coefficient_uncertainties,
        fit.fitted_terms,
        fit.requested_terms,
        units,
        strict=True,
    ):
        if fitted:
            rows.append((name, f"{value:.6g} ± {unc:.2g}  ({u})"))
        elif requested:
            rows.append((name, "0 (not supported by the data; fixed)"))
        else:
            rows.append((name, "0 (term not requested)"))
    rows += [
        ("χ² / ndf", f"{fit.chi2:.3f} / {fit.ndf}"),
        ("Probability", _g(fit.p_value, ".3g") if fit.ndf else "not defined (no redundancy)"),
        ("Valid range", f"{x_name} {lo:.1f}–{hi:.1f}; outside this range widths are extrapolated"),
    ]
    for x in (lo, 0.5 * (lo + hi), hi):
        w, u, _ = fit.fwhm(x)
        text = f"{w[0]:.4g} ± {u[0]:.2g} {unit}"
        if fit.variable is ResolutionVariable.ENERGY_KEV:
            rel, rel_u = fit.relative(x)
            text += f"  ({100 * rel[0]:.3g} ± {100 * rel_u[0]:.2g} % of the energy)"
        rows.append((f"FWHM at {x_name.split()[0]} {x:.1f}", text))
    rows.append(("Uncertainties", "1 standard deviation, statistical; not rescaled by χ²"))
    rows.extend(("Caution", w) for w in fit.warnings)
    return rows


def point_table(fit: ResolutionFit) -> Table:
    unit = fit.unit
    x_name = "Energy (keV)" if fit.variable is ResolutionVariable.ENERGY_KEV else "Channel"
    headers = [x_name, f"FWHM ({unit})", "±", f"Model ({unit})", "Residual", "Residual / σ"]
    model, _, _ = fit.fwhm([p.x for p in fit.points])
    rows = [
        [f"{p.x:.2f}", f"{p.fwhm:.4f}", f"{p.fwhm_unc:.4f}", f"{m:.4f}", f"{r:+.4f}", f"{n:+.2f}"]
        for p, m, r, n in zip(
            fit.points, model, fit.residuals, fit.normalised_residuals, strict=True
        )
    ]
    return headers, rows


def curve(
    fit: ResolutionFit, n: int = 200
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    lo, hi = fit.x_range
    pad = 0.05 * (hi - lo)
    x = np.linspace(max(lo - pad, 1e-6), hi + pad, n)
    w, u, _ = fit.fwhm(x)
    return x, w, u


def resolution_csv_text(spectrum: Spectrum, fit: ResolutionFit) -> str:
    head = [
        "# NSW resolution analysis",
        f"# software: nsw {nsw.__version__}",
        f"# status: {status_text(spectrum, fit)}",
        f"# spectrum: {spectrum_name(spectrum)}",
        f"# data sha256: {spectrum.data_sha256}",
        f"# model: FWHM^2 = a + b x + c x^2; x: {fit.variable.value}; FWHM unit: {fit.unit}",
    ]
    for name, c, u in zip(
        ("a", "b", "c"), fit.coefficients, fit.coefficient_uncertainties, strict=True
    ):
        head.append(f"# {name} = {float(c)!r} +- {float(u)!r}")
    for i, row in enumerate(fit.covariance):
        head.append(f"# covariance row {i}: " + " ".join(repr(float(v)) for v in row))
    head.append(f"# chi2 = {fit.chi2!r}, ndf = {fit.ndf}, p = {fit.p_value!r}")
    head.extend(f"# caution: {w}" for w in fit.warnings)
    head.append("x,fwhm,fwhm_unc,model,residual,normalised_residual")
    model, _, _ = fit.fwhm([p.x for p in fit.points])
    lines = list(head)
    for p, m, r, n in zip(fit.points, model, fit.residuals, fit.normalised_residuals, strict=True):
        cells = (p.x, p.fwhm, p.fwhm_unc, float(m), float(r), float(n))
        lines.append(",".join(repr(float(v)) for v in cells))
    return "\n".join(lines) + "\n"


def export_resolution_csv(
    spectrum: Spectrum,
    fit: ResolutionFit,
    path: Path,
    protected_roots: Iterable[Path] = (),
    overwrite: bool = False,
) -> Path:
    target = ensure_output_allowed(path, protected_roots, overwrite)
    try:
        with target.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as fh:
            fh.write(resolution_csv_text(spectrum, fit))
    except OSError as exc:
        raise ExportError(f"could not write {target}: {exc}") from exc
    return target
