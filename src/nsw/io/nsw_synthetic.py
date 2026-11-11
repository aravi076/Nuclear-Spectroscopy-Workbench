"""Adapter for NSW's own synthetic-spectrum files (``*.nswsyn.json``).

These files are written by ``nsw.simulation`` and contain the sampled counts together
with the complete ground truth. Spectra read from them always have
``origin == SYNTHETIC`` so they can never be mistaken for measurements.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from nsw.core.calibration import CalibrationOrigin, CalibrationStatus, EnergyCalibration
from nsw.core.errors import SpectrumValidationError
from nsw.core.spectrum import DataOrigin, Spectrum, SpectrumKind
from nsw.io.base import MalformedFileError, make_source

FORMAT_TAG = "nsw-synthetic-spectrum"
SUPPORTED_VERSIONS = (1,)


class NswSyntheticAdapter:
    format_name = "NSW synthetic spectrum (JSON)"

    def sniff(self, head: bytes) -> bool:
        return head.lstrip().startswith(b"{") and FORMAT_TAG.encode() in head

    def read(self, path: Path, data: bytes) -> Spectrum:
        try:
            doc: Any = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MalformedFileError(path, f"invalid JSON: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("format") != FORMAT_TAG:
            raise MalformedFileError(path, "not an NSW synthetic spectrum document")
        if doc.get("version") not in SUPPORTED_VERSIONS:
            raise MalformedFileError(path, f"unsupported version {doc.get('version')!r}")
        try:
            truth = doc["truth"]
            role = truth.get("role", "source")
            if role not in ("source", "background"):
                raise ValueError(f"unknown role {role!r}")
            cal = EnergyCalibration(
                coefficients=tuple(truth["energy_calibration_kev"]),
                origin=CalibrationOrigin.SYNTHETIC_TRUTH,
                status=CalibrationStatus.VERIFIED,
                method="generator ground truth",
            )
            return Spectrum(
                counts=np.asarray(doc["counts"]),
                kind=SpectrumKind.BACKGROUND if role == "background" else SpectrumKind.RAW,
                origin=DataOrigin.SYNTHETIC,
                live_time_s=float(doc["live_time_s"]),
                real_time_s=float(doc["real_time_s"]),
                detector_id=str(doc["detector_id"]),
                sample_name=str(doc["synthetic_id"]),
                sample_description=str(truth.get("description", "")) or None,
                energy_calibration=cal,
                source=make_source(path, data, self.format_name, self),
                synthetic_id=str(doc["synthetic_id"]),
                metadata={
                    "synthetic": "true",
                    "detector_type": str(truth.get("detector_type", "")),
                    "seed": str(truth.get("seed", "")),
                    "generator_version": str(truth.get("generator_version", "")),
                },
                warnings=("SYNTHETIC DATA - not a measurement",),
            )
        except (KeyError, TypeError, ValueError, SpectrumValidationError) as exc:
            raise MalformedFileError(path, f"invalid synthetic spectrum: {exc!r}") from exc
