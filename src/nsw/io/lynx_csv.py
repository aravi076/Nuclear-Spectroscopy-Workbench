"""Adapter for the Canberra/Mirion Lynx CSV spectrum export.

Layout (docs/user/file_formats.md), verified on 126 real files:

    Start Time, Tue Mar 10 09:15:27 GMT+0530 2026
    Energy calibration,  Offset: <a>, Slope: <b>, Quadratic: <c>
    Live Time (s), <float>
    Real Time (s), <float>
    Elapsed Computational, <int>
    Spectrum
    Channel, Energy (keV), Counts
    1, <energy>, <counts>
    ...

The energy column and calibration line are vendor metadata and are *not* trusted: the
coefficients are preserved as an UNVERIFIED embedded calibration, the per-channel
energy column is ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from nsw.core.calibration import CalibrationOrigin, EnergyCalibration
from nsw.core.errors import SpectrumValidationError
from nsw.core.spectrum import DataOrigin, Spectrum, SpectrumKind
from nsw.io.base import MalformedFileError, is_whole_seconds, make_source, parse_lynx_timestamp

_HEADER_KEYS = (
    "Start Time",
    "Energy calibration",
    "Live Time (s)",
    "Real Time (s)",
    "Elapsed Computational",
    "Spectrum",
    "Channel",
)
_CAL = re.compile(
    r"Offset:\s*([-+0-9.eE]+)\s*,\s*Slope:\s*([-+0-9.eE]+)\s*,\s*Quadratic:\s*([-+0-9.eE]+)"
)
_INT = re.compile(r"^[+]?\d+$")


class LynxCsvAdapter:
    format_name = "Lynx CSV export"

    def sniff(self, head: bytes) -> bool:
        text = head.removeprefix(b"\xef\xbb\xbf")
        if not text.startswith(b"Start Time,"):
            return False
        return b"Energy calibration" in text or b"Live Time (s)" in text

    def read(self, path: Path, data: bytes) -> Spectrum:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MalformedFileError(path, f"not valid UTF-8 text ({exc})") from exc
        lines = text.splitlines()
        nh = len(_HEADER_KEYS)
        if len(lines) < nh + 1:
            raise MalformedFileError(path, f"file has only {len(lines)} lines; no channel data")
        for i, key in enumerate(_HEADER_KEYS):
            if lines[i].split(",")[0].strip() != key:
                raise MalformedFileError(
                    path, f"header line {i + 1} should start with {key!r}, found {lines[i][:40]!r}"
                )
        warnings: list[str] = []

        try:
            start = parse_lynx_timestamp(lines[0].split(",", 1)[1])
        except (ValueError, IndexError) as exc:
            raise MalformedFileError(path, f"start time: {exc}") from exc

        cal_match = _CAL.search(lines[1])
        if not cal_match:
            raise MalformedFileError(path, f"cannot parse calibration line {lines[1][:60]!r}")
        try:
            coefficients = tuple(float(g) for g in cal_match.groups())
        except ValueError as exc:
            raise MalformedFileError(path, f"calibration coefficients: {exc}") from exc

        live = self._time(path, lines[2], "live time")
        real = self._time(path, lines[3], "real time")

        counts = np.empty(len(lines) - nh, dtype=np.int64)
        for n, line in enumerate(lines[nh:]):
            lineno = n + nh + 1
            if not line.strip():
                raise MalformedFileError(path, f"blank line {lineno} inside the channel table")
            parts = line.split(",")
            if len(parts) != 3:
                raise MalformedFileError(
                    path, f"line {lineno}: expected 3 columns, found {len(parts)}"
                )
            chan, cnt = parts[0].strip(), parts[2].strip()
            if not _INT.match(chan) or int(chan) != n + 1:
                raise MalformedFileError(
                    path, f"line {lineno}: expected channel {n + 1}, found {chan!r}"
                )
            if not _INT.match(cnt):
                raise MalformedFileError(
                    path, f"line {lineno}: counts {cnt!r} is not a non-negative integer"
                )
            counts[n] = int(cnt)

        quantum = 0.0
        if is_whole_seconds(live, real):
            quantum = 1.0
            warnings.append(
                "live and real time are whole seconds; they may have been rounded "
                "(dead time from this file is unreliable)"
            )
        try:
            embedded = EnergyCalibration(
                coefficients=coefficients,
                origin=CalibrationOrigin.IMPORTED_VENDOR,
                method="Lynx CSV header",
                note="vendor calibration as stored in the file; not verified",
            )
            return Spectrum(
                counts=counts,
                kind=SpectrumKind.RAW,
                origin=DataOrigin.EXPERIMENTAL,
                live_time_s=live,
                real_time_s=real,
                start_time=start,
                embedded_calibration=embedded,
                source=make_source(path, data, self.format_name, self),
                time_quantum_s=quantum,
                metadata={"Elapsed Computational": lines[4].split(",", 1)[-1].strip()},
                warnings=tuple(warnings),
            )
        except (SpectrumValidationError, ValueError) as exc:
            raise MalformedFileError(path, str(exc)) from exc

    @staticmethod
    def _time(path: Path, line: str, what: str) -> float:
        try:
            return float(line.split(",")[1])
        except (ValueError, IndexError) as exc:
            raise MalformedFileError(path, f"cannot parse {what} from {line[:40]!r}") from exc
