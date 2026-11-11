"""Use case: write derived outputs (CSV) without ever touching experimental data."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

import nsw
from nsw.core.calibration import CalibrationStatus
from nsw.core.errors import NSWError
from nsw.core.spectrum import Spectrum

CALIBRATION_WARNING = (
    "ENERGY CALIBRATION NOT VERIFIED — ENERGY VALUES MUST NOT BE USED FOR QUANTITATIVE ANALYSIS."
)
SYNTHETIC_NOTICE = "SYNTHETIC DATA — simulated spectrum, not a measurement."


class ExportError(NSWError):
    """An export was refused or failed."""


def ensure_output_allowed(path: Path, protected_roots: Iterable[Path], overwrite: bool) -> Path:
    """Validate an output location. Raises :class:`ExportError`; returns the resolved path."""
    target = path.resolve()
    for root in protected_roots:
        try:
            target.relative_to(root.resolve())
        except ValueError:
            continue
        raise ExportError(f"refusing to write inside the read-only experimental dataset: {root}")
    if target.exists() and not overwrite:
        raise ExportError(f"file already exists: {target}")
    if not target.parent.is_dir():
        raise ExportError(f"folder does not exist: {target.parent}")
    return target


def spectrum_csv_text(spectrum: Spectrum) -> str:
    """CSV text: commented provenance header, then channel, value, standard uncertainty.

    The x-axis is always the channel number. No energy column is written unless the
    spectrum carries a verified calibration.
    """
    s = spectrum
    verified = s.calibration_status is CalibrationStatus.VERIFIED
    head = [
        "# NSW spectrum export",
        f"# software: nsw {nsw.__version__}",
        f"# origin: {s.origin.value}",
        f"# kind: {s.kind.value}",
        f"# measurement status: {s.measurement_status.value}",
    ]
    if s.is_synthetic:
        head.append(f"# {SYNTHETIC_NOTICE}")
        head.append(f"# synthetic id: {s.synthetic_id}")
    if s.source is not None:
        head.append(f"# source file: {s.source.filename}")
        head.append(f"# source file sha256: {s.source.sha256}")
        head.append(f"# source format: {s.source.format}")
    head += [
        f"# data sha256: {s.data_sha256}",
        f"# detector: {s.detector_id or 'not recorded'}",
        f"# sample: {s.sample_name or 'not recorded'}",
        f"# start time: {s.start_time.isoformat() if s.start_time else 'not recorded'}",
        f"# live time (s): {s.live_time_s if s.live_time_s is not None else 'not recorded'}",
        f"# real time (s): {s.real_time_s if s.real_time_s is not None else 'not recorded'}",
    ]
    if s.time_quantum_s > 0:
        head.append(f"# recorded times are rounded to {s.time_quantum_s:g} s")
    for step in s.provenance:
        params = ", ".join(f"{k}={v}" for k, v in step.parameters.items())
        head.append(f"# processing: {step.operation} ({params})")
    if verified:
        head.append("# energy calibration: verified")
        columns = "channel,energy_keV,value,uncertainty"
    else:
        head.append(f"# {CALIBRATION_WARNING}")
        head.append("# x-axis: channel number (1-based)")
        columns = "channel,value,uncertainty"
    head.append("# uncertainty: one standard deviation (sqrt(N) for count data)")
    head.append(columns)

    unc = s.uncertainty_array()
    integer = s.is_count_data
    rows: list[str] = []
    energies = s.energies() if verified else None
    for i, ch in enumerate(s.channels):
        value = str(int(s.counts[i])) if integer else repr(float(s.counts[i]))
        mid = f"{float(energies[i])!r}," if energies is not None else ""
        rows.append(f"{int(ch)},{mid}{value},{float(unc[i])!r}")
    return "\n".join(head + rows) + "\n"


def export_spectrum_csv(
    spectrum: Spectrum,
    path: Path,
    protected_roots: Iterable[Path] = (),
    overwrite: bool = False,
) -> Path:
    target = ensure_output_allowed(path, protected_roots, overwrite)
    text = spectrum_csv_text(spectrum)
    try:
        with target.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        raise ExportError(f"could not write {target}: {exc}") from exc
    return target


def read_exported_values(path: Path) -> NDArray[np.float64]:
    """Read back the value column of an NSW CSV export (used by tests and checks)."""
    rows = [
        ln for ln in path.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")
    ]
    header = rows[0].split(",")
    col = header.index("value")
    return np.array([float(r.split(",")[col]) for r in rows[1:]])
