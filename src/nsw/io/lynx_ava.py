"""Adapter for the Canberra/Mirion Lynx XML spectrum file (``.ava``).

Root element ``<mcadata xmlns="http://www.canberra.com/mcaanywhair">``. Verified on 50
real files. All ``<parameter>`` elements are preserved verbatim in ``metadata``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from nsw.core.calibration import CalibrationOrigin, EnergyCalibration
from nsw.core.errors import SpectrumValidationError
from nsw.core.spectrum import DataOrigin, Spectrum, SpectrumKind
from nsw.io.base import MalformedFileError, is_whole_seconds, make_source, parse_lynx_timestamp

NAMESPACE = "http://www.canberra.com/mcaanywhair"
_NS = "{" + NAMESPACE + "}"

# (code, name) of parameters NSW interprets; everything else is only preserved.
P_DETECTOR_LABEL = ("0", "Detector Name")
P_SAMPLE_NAME = ("2", "Sample Name")
P_SAMPLE_DESCRIPTION = ("3", "Sample Description")
P_MCA_NAME = ("81", "Detector Name")
P_SERIAL = ("80", "Serial Number")
P_PRESET_LIVE = ("39", "Preset Live Time (s)")
P_CONVERSION_GAIN = ("70", "Conversion Gain")


class LynxAvaAdapter:
    format_name = "Lynx XML (.ava)"

    def sniff(self, head: bytes) -> bool:
        return b"<mcadata" in head and NAMESPACE.encode() in head

    def read(self, path: Path, data: bytes) -> Spectrum:
        # Entity declarations are never present in genuine files; refuse them outright
        # rather than let the XML parser expand them.
        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            raise MalformedFileError(path, "XML document type / entity declarations not allowed")
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise MalformedFileError(path, f"invalid XML: {exc}") from exc
        if root.tag != _NS + "mcadata":
            raise MalformedFileError(path, f"unexpected root element {root.tag!r}")

        warnings: list[str] = []
        metadata: dict[str, str] = {}
        params: dict[tuple[str, str], str] = {}
        for el in root.iter(_NS + "parameter"):
            code, name, value = el.get("code", ""), el.get("name", ""), el.get("value", "")
            params.setdefault((code, name), value)
            key = f"[{code}] {name}"
            n = 2
            while key in metadata:  # (code, name) is not unique in this format
                key = f"[{code}] {name} #{n}"
                n += 1
            metadata[key] = value

        spec = root.find(_NS + "spectrum")
        if spec is None:
            raise MalformedFileError(path, "no <spectrum> element")
        try:
            counts = np.array(
                [int(tok) for tok in (spec.text or "").split(",") if tok.strip()], dtype=np.int64
            )
        except ValueError as exc:
            raise MalformedFileError(path, f"non-integer channel content: {exc}") from exc
        if counts.size == 0:
            raise MalformedFileError(path, "<spectrum> contains no channel data")

        live = self._float_attr(path, spec, "elapsed_live")
        real = self._float_attr(path, spec, "elapsed_real")
        start = self._start_time(path, spec, warnings)
        for attr in ("elapsed_deadtime", "status", "units"):
            if spec.get(attr) is not None:
                metadata[f"spectrum.{attr}"] = str(spec.get(attr))

        gain = params.get(P_CONVERSION_GAIN)
        if gain is not None and gain.isdigit() and int(gain) != counts.size:
            warnings.append(
                f"conversion gain parameter is {gain} but the file holds {counts.size} channels"
            )
        preset = params.get(P_PRESET_LIVE)
        if preset is not None:
            metadata["preset_live_time_s"] = preset

        embedded = self._embedded_calibration(root, warnings)
        if embedded is None:
            warnings.append("no usable energy calibration element in file")

        quantum = 0.0
        if is_whole_seconds(live, real):
            quantum = 1.0
            warnings.append("live and real time are whole seconds; they may have been rounded")

        label = params.get(P_DETECTOR_LABEL)
        mca = params.get(P_MCA_NAME)
        serial = params.get(P_SERIAL)
        detector_id = f"{label or 'detector'}@{mca or 'MCA'}-{serial}" if serial else None
        try:
            return Spectrum(
                counts=counts,
                kind=SpectrumKind.RAW,
                origin=DataOrigin.EXPERIMENTAL,
                live_time_s=live,
                real_time_s=real,
                start_time=start,
                detector_id=detector_id,
                sample_name=params.get(P_SAMPLE_NAME) or None,
                sample_description=params.get(P_SAMPLE_DESCRIPTION) or None,
                embedded_calibration=embedded,
                source=make_source(path, data, self.format_name, self),
                time_quantum_s=quantum,
                metadata=metadata,
                warnings=tuple(warnings),
            )
        except SpectrumValidationError as exc:
            raise MalformedFileError(path, str(exc)) from exc

    @staticmethod
    def _float_attr(path: Path, el: ET.Element, name: str) -> float:
        raw = el.get(name)
        if raw is None:
            raise MalformedFileError(path, f"<spectrum> lacks attribute {name!r}")
        try:
            return float(raw)
        except ValueError as exc:
            raise MalformedFileError(path, f"<spectrum> {name}={raw!r} is not a number") from exc

    @staticmethod
    def _start_time(path: Path, spec: ET.Element, warnings: list[str]) -> datetime | None:
        text, ms = spec.get("start"), spec.get("start_time_ms")
        zoned: datetime | None = None
        if text:
            try:
                zoned = parse_lynx_timestamp(text)
            except ValueError:
                warnings.append(f"unparseable start time text {text!r}")
        if ms is not None:
            try:
                exact = datetime.fromtimestamp(int(ms) / 1000.0, tz=UTC)
            except (ValueError, OverflowError, OSError) as exc:
                raise MalformedFileError(path, f"start_time_ms={ms!r} invalid") from exc
            if zoned is None:
                warnings.append("start time zone unknown; shown in UTC")
                return exact
            exact = exact.astimezone(zoned.tzinfo)
            if abs((exact - zoned).total_seconds()) > 1.0:
                warnings.append(
                    f"start text ({text}) and start_time_ms ({exact.isoformat()}) disagree"
                )
            return exact
        if zoned is None:
            warnings.append("no acquisition start time in file")
        return zoned

    @staticmethod
    def _embedded_calibration(root: ET.Element, warnings: list[str]) -> EnergyCalibration | None:
        for details in root.iter(_NS + "calibration_details"):
            if details.get("type") != "energy":
                continue
            for model in details.iter(_NS + "model"):
                if model.get("type") != "polynomial":
                    continue
                try:
                    coeffs = tuple(
                        float(c.get("value", "nan")) for c in model.iter(_NS + "coefficient")
                    )
                    return EnergyCalibration(
                        coefficients=coeffs,
                        origin=CalibrationOrigin.IMPORTED_VENDOR,
                        method="Lynx XML calibration_details[energy]",
                        note="vendor calibration as stored in the file; not verified",
                    )
                except ValueError as exc:
                    warnings.append(f"embedded energy calibration unreadable: {exc}")
                    return None
        return None
