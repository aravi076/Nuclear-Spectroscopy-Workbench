"""The validated, immutable spectrum data model.

Conventions
-----------
* Channels are 1-based: ``channels[i] == first_channel + i`` with ``first_channel == 1``
  for instrument data.
* *Count data* (kinds RAW and BACKGROUND) hold non-negative ``int64`` counts and have
  Poisson statistics. *Derived data* (NET, PROCESSED) hold ``float64`` values, may be
  negative, and must carry an explicit per-channel variance.
* ``origin`` (experimental / synthetic) is orthogonal to ``kind`` so that a synthetic
  background or a synthetic net spectrum can never be mistaken for a measurement.
* A spectrum is never modified. Every operation returns a new object whose
  ``provenance`` records what was done and to which parent data.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from nsw.core.calibration import CalibrationOrigin, CalibrationStatus, EnergyCalibration
from nsw.core.errors import CalibrationNotVerifiedError, SpectrumValidationError


class SpectrumKind(StrEnum):
    RAW = "raw"
    BACKGROUND = "background"
    NET = "net"
    PROCESSED = "processed"


class DataOrigin(StrEnum):
    EXPERIMENTAL = "experimental"
    SYNTHETIC = "synthetic"


class MeasurementStatus(StrEnum):
    UNCLASSIFIED = "unclassified"  # identity analysis has not been run
    INDEPENDENT = "independent"  # a complete acquisition in its own right
    INTERMEDIATE_SAVE = "intermediate-save"  # earlier save of a still-running acquisition
    DERIVED = "derived"  # computed from other spectra


COUNT_KINDS = frozenset({SpectrumKind.RAW, SpectrumKind.BACKGROUND})

# Live time may exceed real time only by this relative amount (floating-point export noise).
_LIVE_REAL_RTOL = 1e-6


@dataclass(frozen=True, slots=True)
class SourceFile:
    """Provenance of the file a spectrum was read from."""

    path: str  # absolute path at import time
    filename: str  # original file name, preserved verbatim
    size_bytes: int
    sha256: str
    format: str  # adapter-reported format name
    adapter: str  # adapter class that produced the spectrum

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise SpectrumValidationError("sha256 must be a 64-character lowercase hex digest")
        if self.size_bytes < 0:
            raise SpectrumValidationError("size_bytes must be >= 0")
        if not self.filename:
            raise SpectrumValidationError("filename must not be empty")


@dataclass(frozen=True, slots=True)
class ProcessingStep:
    """One entry of a spectrum's processing history."""

    operation: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    parents: tuple[str, ...] = ()  # data hashes of the input spectra
    note: str = ""

    def __post_init__(self) -> None:
        if not self.operation:
            raise SpectrumValidationError("processing step needs an operation name")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


def _frozen_array(values: ArrayLike, dtype: type[np.generic], name: str) -> NDArray[Any]:
    arr = np.asarray(values)
    if arr.ndim != 1:
        raise SpectrumValidationError(f"{name} must be one-dimensional, got shape {arr.shape}")
    if arr.size == 0:
        raise SpectrumValidationError(f"{name} must not be empty")
    if arr.dtype == object or arr.dtype.kind not in "iuf":
        raise SpectrumValidationError(f"{name} must be numeric, got dtype {arr.dtype}")
    if arr.dtype.kind == "f" and not np.all(np.isfinite(arr)):
        raise SpectrumValidationError(f"{name} contains NaN or infinite values")
    if dtype is np.int64 and arr.dtype.kind == "f":
        if np.any(arr != np.floor(arr)):
            raise SpectrumValidationError(f"{name} must be whole numbers for count data")
        if np.any(np.abs(arr) > 2**53):
            raise SpectrumValidationError(f"{name} exceeds exactly representable integer range")
    out = np.array(arr, dtype=dtype, copy=True)  # always an owned copy
    out.setflags(write=False)
    return out


@dataclass(frozen=True, eq=False)
class Spectrum:
    """An immutable pulse-height spectrum with metadata and provenance."""

    counts: NDArray[Any]
    kind: SpectrumKind
    origin: DataOrigin
    live_time_s: float | None = None
    real_time_s: float | None = None
    start_time: datetime | None = None  # tz-aware, or naive = instrument local wall clock
    detector_id: str | None = None
    sample_name: str | None = None
    sample_description: str | None = None
    variance: NDArray[np.float64] | None = None
    first_channel: int = 1
    embedded_calibration: EnergyCalibration | None = None  # as found in the file; untrusted
    energy_calibration: EnergyCalibration | None = None  # calibration assigned by NSW
    source: SourceFile | None = None
    synthetic_id: str | None = None  # identifier of the ground-truth record
    time_quantum_s: float = 0.0  # >0 if recorded times are rounded to this step
    measurement_status: MeasurementStatus = MeasurementStatus.UNCLASSIFIED
    metadata: Mapping[str, str] = field(default_factory=dict)
    provenance: tuple[ProcessingStep, ...] = ()
    warnings: tuple[str, ...] = ()
    data_sha256: str = field(init=False, default="")

    # ------------------------------------------------------------------ validation
    def __post_init__(self) -> None:
        setter = object.__setattr__
        if not isinstance(self.kind, SpectrumKind):
            raise SpectrumValidationError(f"kind must be a SpectrumKind, got {self.kind!r}")
        if not isinstance(self.origin, DataOrigin):
            raise SpectrumValidationError(f"origin must be a DataOrigin, got {self.origin!r}")

        is_counts = self.kind in COUNT_KINDS
        counts = _frozen_array(self.counts, np.int64 if is_counts else np.float64, "counts")
        if is_counts and np.any(counts < 0):
            raise SpectrumValidationError(
                f"{self.kind.value} spectrum has negative counts "
                f"(first at channel {self.first_channel + int(np.argmax(counts < 0))})"
            )
        setter(self, "counts", counts)

        if is_counts:
            if self.variance is not None:
                raise SpectrumValidationError(
                    "count data have Poisson variance; an explicit variance is not allowed"
                )
        else:
            if self.variance is None:
                raise SpectrumValidationError(
                    f"{self.kind.value} spectrum requires an explicit per-channel variance"
                )
            var = _frozen_array(self.variance, np.float64, "variance")
            if var.shape != counts.shape:
                raise SpectrumValidationError(
                    f"variance length {var.size} does not match counts length {counts.size}"
                )
            if np.any(var < 0):
                raise SpectrumValidationError("variance must be non-negative")
            setter(self, "variance", var)
            if not self.provenance:
                raise SpectrumValidationError(
                    f"{self.kind.value} spectrum must record how it was derived (provenance)"
                )

        if isinstance(self.first_channel, bool) or not isinstance(self.first_channel, int):
            raise SpectrumValidationError("first_channel must be an integer")
        if self.first_channel < 0:
            raise SpectrumValidationError("first_channel must be >= 0")

        for name in ("live_time_s", "real_time_s"):
            t = getattr(self, name)
            if t is not None and (not math.isfinite(t) or t <= 0.0):
                raise SpectrumValidationError(f"{name} must be finite and > 0, got {t!r}")
        if (
            self.live_time_s is not None
            and self.real_time_s is not None
            and self.live_time_s > self.real_time_s * (1.0 + _LIVE_REAL_RTOL) + self.time_quantum_s
        ):
            raise SpectrumValidationError(
                f"live time ({self.live_time_s} s) exceeds real time ({self.real_time_s} s)"
            )
        if not math.isfinite(self.time_quantum_s) or self.time_quantum_s < 0:
            raise SpectrumValidationError("time_quantum_s must be finite and >= 0")

        # --- origin rules: experimental and synthetic data must stay distinguishable
        if self.origin is DataOrigin.SYNTHETIC:
            if not self.synthetic_id:
                raise SpectrumValidationError("synthetic spectrum requires a synthetic_id")
        else:
            if self.synthetic_id is not None:
                raise SpectrumValidationError("experimental spectrum cannot have a synthetic_id")
            if is_counts and self.source is None:
                raise SpectrumValidationError(
                    "experimental count spectrum requires file provenance (source)"
                )

        # --- calibration rules
        emb = self.embedded_calibration
        if emb is not None:
            if emb.origin is not CalibrationOrigin.IMPORTED_VENDOR:
                raise SpectrumValidationError(
                    "embedded_calibration must have origin IMPORTED_VENDOR"
                )
            if emb.is_verified:  # cannot happen via the model, guard anyway
                raise SpectrumValidationError("an embedded calibration can never be verified")
        cal = self.energy_calibration
        if cal is not None:
            if cal.origin is CalibrationOrigin.IMPORTED_VENDOR:
                raise SpectrumValidationError(
                    "a vendor calibration may only be stored as embedded_calibration"
                )
            if (
                cal.origin is CalibrationOrigin.SYNTHETIC_TRUTH
                and self.origin is not DataOrigin.SYNTHETIC
            ):
                raise SpectrumValidationError(
                    "synthetic-truth calibration on an experimental spectrum"
                )

        setter(
            self, "metadata", MappingProxyType({str(k): str(v) for k, v in self.metadata.items()})
        )
        setter(self, "provenance", tuple(self.provenance))
        setter(self, "warnings", tuple(self.warnings))
        setter(self, "data_sha256", self._compute_hash())

    # ------------------------------------------------------------------ identity
    def _compute_hash(self) -> str:
        h = hashlib.sha256()
        h.update(self.counts.dtype.str.encode())
        h.update(np.ascontiguousarray(self.counts).astype(self.counts.dtype.newbyteorder("<")))
        if self.variance is not None:
            h.update(np.ascontiguousarray(self.variance).astype("<f8"))
        return h.hexdigest()

    def verify_integrity(self) -> bool:
        """True if the channel data still match the hash taken at construction."""
        return self._compute_hash() == self.data_sha256

    # ------------------------------------------------------------------ basic views
    @property
    def n_channels(self) -> int:
        return int(self.counts.size)

    @property
    def channels(self) -> NDArray[np.int64]:
        return np.arange(self.first_channel, self.first_channel + self.n_channels, dtype=np.int64)

    @property
    def is_count_data(self) -> bool:
        return self.kind in COUNT_KINDS

    @property
    def is_synthetic(self) -> bool:
        return self.origin is DataOrigin.SYNTHETIC

    @property
    def total(self) -> float:
        """Sum over all channels (an exact integer for count data)."""
        return float(self.counts.sum())

    def variance_array(self) -> NDArray[np.float64]:
        """Per-channel variance: observed counts for count data, stored variance otherwise.

        For count data this is the usual plug-in estimate var = N, which is zero for
        empty channels; see docs/scientific_methodology.md for its limitations.
        """
        if self.variance is not None:
            return self.variance
        out = self.counts.astype(np.float64)
        out.setflags(write=False)
        return out

    def uncertainty_array(self) -> NDArray[np.float64]:
        return np.sqrt(self.variance_array())

    @property
    def dead_time_fraction(self) -> float | None:
        """1 - live/real, or None when either time is missing."""
        if self.live_time_s is None or self.real_time_s is None:
            return None
        return max(0.0, 1.0 - self.live_time_s / self.real_time_s)

    @property
    def start_time_local(self) -> datetime | None:
        """Start time as instrument wall-clock time without zone (for cross-format matching)."""
        if self.start_time is None:
            return None
        return self.start_time.replace(tzinfo=None)

    # ------------------------------------------------------------------ calibration
    @property
    def calibration_status(self) -> CalibrationStatus:
        if self.energy_calibration is not None and self.energy_calibration.is_verified:
            return CalibrationStatus.VERIFIED
        if self.energy_calibration is not None or self.embedded_calibration is not None:
            return CalibrationStatus.UNVERIFIED
        return CalibrationStatus.NONE

    def energies(self) -> NDArray[np.float64]:
        """Channel energies in keV. Raises unless a *verified* calibration is assigned."""
        if self.energy_calibration is None or not self.energy_calibration.is_verified:
            raise CalibrationNotVerifiedError(
                f"spectrum has calibration status {self.calibration_status.value!r}; "
                "use channel numbers"
            )
        return self.energy_calibration.energy_at(self.channels)

    # ------------------------------------------------------------------ diagnostics
    def missing_metadata(self) -> tuple[str, ...]:
        """Names of metadata items that are absent and limit what analyses are possible."""
        missing: list[str] = []
        if self.live_time_s is None:
            missing.append("live time")
        if self.real_time_s is None:
            missing.append("real time")
        if self.start_time is None:
            missing.append("acquisition start time")
        if not self.detector_id:
            missing.append("detector identifier")
        if not self.sample_name:
            missing.append("sample name")
        if self.calibration_status is not CalibrationStatus.VERIFIED:
            missing.append("verified energy calibration")
        return tuple(missing)

    # ------------------------------------------------------------------ derivations
    def evolve(self, **changes: Any) -> Spectrum:
        """Return a copy with *metadata-level* fields replaced. Channel data cannot change."""
        forbidden = {"counts", "variance", "kind", "origin", "data_sha256", "first_channel"}
        bad = forbidden & set(changes)
        if bad:
            raise SpectrumValidationError(
                f"evolve() cannot change {sorted(bad)}; create a derived spectrum instead"
            )
        return dataclasses.replace(self, **changes)

    def designated_as_background(self, note: str = "") -> Spectrum:
        """The same measured counts, explicitly given the BACKGROUND role by the analyst."""
        if self.kind is SpectrumKind.BACKGROUND:
            return self
        if self.kind is not SpectrumKind.RAW:
            raise SpectrumValidationError("only a raw spectrum can be designated as background")
        step = ProcessingStep("designate-background", parents=(self.data_sha256,), note=note)
        return dataclasses.replace(
            self, kind=SpectrumKind.BACKGROUND, provenance=(*self.provenance, step)
        )

    def __repr__(self) -> str:
        name = self.source.filename if self.source else (self.synthetic_id or "derived")
        return (
            f"<Spectrum {self.origin.value}/{self.kind.value} {name!r} "
            f"{self.n_channels} ch, total={self.total:g}, live={self.live_time_s}>"
        )
