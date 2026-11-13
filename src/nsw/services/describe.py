"""Use case: turn a spectrum into labelled text rows for metadata panels and reports.

Nothing here computes physics; missing items are reported as missing, never filled in.
"""

from __future__ import annotations

import math

from nsw.core.calibration import CalibrationStatus
from nsw.core.spectrum import Spectrum

Rows = list[tuple[str, str]]
NOT_RECORDED = "not recorded"

# AVA parameter key -> label shown in the detector panel
_HARDWARE_KEYS: tuple[tuple[str, str], ...] = (
    ("[0] Detector Name", "Detector label (operator-entered)"),
    ("[1] Detector Description", "Detector description"),
    ("[81] Detector Name", "MCA name"),
    ("[80] Serial Number", "MCA serial number"),
    ("[203] Firmware Version", "MCA firmware"),
    ("[70] Conversion Gain", "Conversion gain (channels)"),
    ("[158] HV Voltage (V)", "HV setting (V)"),
    ("[166] HV Reading (V)", "HV reading (V)"),
    ("[154] Coarse Gain", "Coarse gain"),
    ("[145] Fine Gain", "Fine gain"),
    ("[194] Rise Time (μs)", "Shaping rise time (µs)"),
    ("[195] Flat Top (μs)", "Shaping flat top (µs)"),
    ("[241] LLD %", "LLD (%)"),
    ("[242] ULD %", "ULD (%)"),
    ("[243] PUR Guard", "Pile-up rejector guard"),
)


def tidy_number(text: str) -> str:
    """A decimal setting as the instrument meant it. Vendor files store many settings as
    32-bit floats written out in full (8.800000190734863 for 8.8); 7 significant digits
    is the precision of such a float. Anything that is not a plain decimal is unchanged."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return text
    if not math.isfinite(value) or "." not in text or len(text.split(".")[1]) <= 6:
        return text
    return f"{value:.7g}"


def _fmt_time(seconds: float | None) -> str:
    return NOT_RECORDED if seconds is None else f"{seconds:.6g} s"


def measurement_rows(s: Spectrum) -> Rows:
    dead = s.dead_time_fraction
    rows: Rows = [
        ("Data origin", s.origin.value.upper()),
        ("Spectrum kind", s.kind.value),
        ("Measurement status", s.measurement_status.value),
        ("Sample name", s.sample_name or NOT_RECORDED),
        ("Sample description", s.sample_description or NOT_RECORDED),
        ("Acquisition start", s.start_time.isoformat(sep=" ") if s.start_time else NOT_RECORDED),
        ("Live time", _fmt_time(s.live_time_s)),
        ("Real time", _fmt_time(s.real_time_s)),
        ("Dead time", NOT_RECORDED if dead is None else f"{100.0 * dead:.2f} %"),
        ("Channels", f"{s.n_channels} (first channel {s.first_channel})"),
        ("Total counts" if s.is_count_data else "Sum of values", f"{s.total:,.0f}"),
    ]
    if s.start_time is not None and s.start_time.tzinfo is None:
        rows.append(("Start time zone", "none recorded (instrument local time)"))
    if s.time_quantum_s > 0:
        rows.append(
            ("Timing precision", f"times rounded to {s.time_quantum_s:g} s; dead time unreliable")
        )
    if "preset_live_time_s" in s.metadata:
        rows.append(("Preset live time", f"{s.metadata['preset_live_time_s']} s"))
    rows.extend(calibration_rows(s))
    missing = s.missing_metadata()
    rows.append(("Missing metadata", "; ".join(missing) if missing else "none"))
    return rows


def calibration_rows(s: Spectrum) -> Rows:
    status = s.calibration_status
    rows: Rows = [("Energy calibration status", status.value.upper())]
    if s.embedded_calibration is not None:
        coeffs = ", ".join(f"{c:.9g}" for c in s.embedded_calibration.coefficients)
        rows.append(("Vendor calibration in file (NOT VERIFIED, not applied)", coeffs))
    if s.energy_calibration is not None:
        cal = s.energy_calibration
        coeffs = ", ".join(f"{c:.9g}" for c in cal.coefficients)
        rows.append((f"Assigned calibration ({cal.origin.value}, {cal.status.value})", coeffs))
    if status is CalibrationStatus.NONE:
        rows.append(("Calibration", "none available"))
    return rows


def detector_rows(s: Spectrum) -> Rows:
    rows: Rows = [("Detector identifier", s.detector_id or NOT_RECORDED)]
    if s.is_synthetic:
        rows.append(("Detector type", s.metadata.get("detector_type", NOT_RECORDED)))
        rows.append(("Note", "simulated detector with assumed parameters"))
        return rows
    found = False
    for key, label in _HARDWARE_KEYS:
        if key in s.metadata:
            rows.append((label, tidy_number(s.metadata[key])))
            found = True
    if not found:
        rows.append(
            ("Hardware settings", "not recorded in this file format (present in .ava files only)")
        )
    rows.append(("Detector model / crystal dimensions", NOT_RECORDED))
    rows.append(("Source-detector geometry", NOT_RECORDED))
    return rows


def file_rows(s: Spectrum, other_files: tuple[str, ...] = (), *, include_path: bool = True) -> Rows:
    """``include_path=False`` for documents that leave this computer (reports): the
    folder can contain personal names; the file is identified by name and SHA-256."""
    if s.source is None:
        return [("File", "none (generated or derived in memory)")]
    src = s.source
    rows: Rows = [("Original file name", src.filename)]
    if include_path:
        rows.append(("Path", src.path))
    rows += [
        ("Format", src.format),
        ("Adapter", src.adapter),
        ("Size", f"{src.size_bytes:,} bytes"),
        ("File SHA-256", src.sha256),
        ("Channel-data SHA-256", s.data_sha256),
    ]
    for name in other_files:
        rows.append(("Same data also in", name))
    return rows


def history_rows(s: Spectrum) -> Rows:
    rows: Rows = [("Warning", w) for w in s.warnings]
    for i, step in enumerate(s.provenance, start=1):
        params = ", ".join(f"{k}={v}" for k, v in step.parameters.items())
        text = step.operation + (f" ({params})" if params else "")
        if step.note:
            text += f" - {step.note}"
        rows.append((f"Processing step {i}", text))
    if not rows:
        rows.append(("History", "raw data as imported; no processing applied"))
    return rows
