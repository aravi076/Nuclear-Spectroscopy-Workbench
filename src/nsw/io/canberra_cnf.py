"""Adapter for Canberra/Mirion CAM "configuration" files (``.cnf``).

The format is not publicly specified. The layout used here comes from community
reverse-engineering and was checked during development against private instrument
files: for every CNF file with a same-acquisition Lynx CSV export the decoded counts
were bit-identical. Only fields covered by that check are decoded:

* section directory: 48-byte entries from 0x70; uint32 id at +0, uint32 offset at +0x0A
* 0x00012000 acquisition parameters: record offsets, channel count, energy coefficients,
  start / real / live time
* 0x00012001 sample: name
* 0x00012005 channel data: uint32 counts from +0x200

Times in the audited files are whole seconds, and the start time is the instrument's
local wall clock without zone information.
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from nsw.core.calibration import CalibrationOrigin, EnergyCalibration
from nsw.core.errors import SpectrumValidationError
from nsw.core.spectrum import DataOrigin, Spectrum, SpectrumKind
from nsw.io.base import MalformedFileError, is_whole_seconds, make_source

_DIR_START, _DIR_ENTRY, _DIR_END = 0x70, 0x30, 0x800
SEC_ACQ, SEC_SAMPLE, SEC_DATA = 0x00012000, 0x00012001, 0x00012005
_VMS_EPOCH = datetime(1858, 11, 17)  # Modified Julian Date zero
_MAX_SECONDS = 10 * 365.25 * 86400.0  # sanity bound for elapsed times


def _pdp11_float(b: bytes, off: int) -> float:
    """PDP-11 F-float: swap the two 16-bit words, read IEEE float32, divide by 4."""
    return float(struct.unpack("<f", b[off + 2 : off + 4] + b[off : off + 2])[0]) / 4.0


def _elapsed_seconds(b: bytes, off: int) -> float:
    """Elapsed time stored as a negative count of 100 ns ticks (two's complement int64)."""
    return float(-struct.unpack_from("<q", b, off)[0]) * 1e-7


class CanberraCnfAdapter:
    format_name = "Canberra CAM (.cnf)"

    def sniff(self, head: bytes) -> bool:
        if len(head) < _DIR_START + _DIR_ENTRY:
            return False
        return bool(struct.unpack_from("<I", head, _DIR_START)[0] == SEC_ACQ)

    def read(self, path: Path, data: bytes) -> Spectrum:
        try:
            return self._read(path, data)
        except struct.error as exc:
            raise MalformedFileError(path, f"file truncated ({exc})") from exc
        except SpectrumValidationError as exc:
            raise MalformedFileError(path, str(exc)) from exc

    def _read(self, path: Path, b: bytes) -> Spectrum:
        size = len(b)
        sections: dict[int, int] = {}
        for off in range(_DIR_START, min(_DIR_END, size - _DIR_ENTRY), _DIR_ENTRY):
            sid = struct.unpack_from("<I", b, off)[0]
            if sid == 0:
                break
            loc = struct.unpack_from("<I", b, off + 0x0A)[0]
            if loc >= size:
                raise MalformedFileError(
                    path, f"section 0x{sid:08x} points beyond the end of the file"
                )
            sections.setdefault(sid, loc)  # first occurrence, as in the validated decoder
        for sid, what in ((SEC_ACQ, "acquisition parameter"), (SEC_DATA, "channel data")):
            if sid not in sections:
                raise MalformedFileError(path, f"no {what} section (0x{sid:08x})")

        acq = sections[SEC_ACQ]
        rec = acq + 0x30 + struct.unpack_from("<H", b, acq + 0x22)[0]
        tim = acq + 0x30 + struct.unpack_from("<H", b, acq + 0x24)[0]
        n_channels = b[acq + 0xBA] * 256
        if n_channels == 0:
            raise MalformedFileError(path, "channel count field is zero")
        data_off = sections[SEC_DATA] + 0x200
        if data_off + 4 * n_channels > size:
            raise MalformedFileError(
                path, f"{n_channels} channels do not fit in a file of {size} bytes"
            )
        counts = np.frombuffer(b, dtype="<u4", count=n_channels, offset=data_off).astype(np.int64)

        warnings: list[str] = []
        live = _elapsed_seconds(b, tim + 0x11)
        real = _elapsed_seconds(b, tim + 0x09)
        for name, t in (("live", live), ("real", real)):
            if not 0.0 < t < _MAX_SECONDS:
                raise MalformedFileError(path, f"implausible {name} time {t!r} s")
        # Stored in 100 ns ticks; remove binary noise below one tick.
        live, real = round(live, 7), round(real, 7)
        quantum = 0.0
        if is_whole_seconds(live, real):
            quantum = 1.0
            warnings.append(
                "CNF live and real time are whole seconds (rounded by the acquisition "
                "software); dead time from this file is unreliable"
            )

        start: datetime | None = None
        ticks = struct.unpack_from("<Q", b, tim + 0x01)[0]
        try:
            start = _VMS_EPOCH + timedelta(microseconds=ticks // 10)
            if not 1990 <= start.year <= 2100:
                raise OverflowError
        except OverflowError:
            start = None
            warnings.append("start time field is not a plausible date; ignored")
        else:
            warnings.append("start time is instrument local time without zone information")

        embedded: EnergyCalibration | None = None
        try:
            coeffs = tuple(_pdp11_float(b, rec + 0x44 + 4 * i) for i in range(3))
            embedded = EnergyCalibration(
                coefficients=coeffs,
                origin=CalibrationOrigin.IMPORTED_VENDOR,
                method="CNF acquisition parameters",
                note="vendor calibration as stored in the file; not verified",
            )
        except ValueError as exc:
            warnings.append(f"embedded energy calibration unreadable: {exc}")

        sample_name: str | None = None
        if SEC_SAMPLE in sections:
            raw = b[sections[SEC_SAMPLE] + 0x30 : sections[SEC_SAMPLE] + 0x70]
            sample_name = raw.split(b"\x00")[0].decode("latin-1").strip() or None

        return Spectrum(
            counts=counts,
            kind=SpectrumKind.RAW,
            origin=DataOrigin.EXPERIMENTAL,
            live_time_s=live,
            real_time_s=real,
            start_time=start,
            sample_name=sample_name,
            embedded_calibration=embedded,
            source=make_source(path, b, self.format_name, self),
            time_quantum_s=quantum,
            metadata={"cnf.sections": ", ".join(f"0x{s:08x}" for s in sections)},
            warnings=tuple(warnings),
        )
