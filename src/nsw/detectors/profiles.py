"""Detector profiles for HPGe and NaI(Tl) systems.

The two detector families have separate profile classes so that neither resolution nor
efficiency parameters can be shared by accident. A profile stores what is *known* about
a detector; anything not supported by evidence stays ``None``. Nominal expectations
(e.g. typical resolution ranges) are class-level plausibility bounds used only to warn,
never as analysis inputs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from nsw.core.calibration import (
    EfficiencyCalibration,
    EnergyCalibration,
    ResolutionCalibration,
)


class DetectorType(StrEnum):
    HPGE = "hpge"
    NAI_TL = "nai-tl"


class EvidenceLevel(StrEnum):
    """How the detector type of a profile was established."""

    MEASURED = "measured"  # from measured peak widths
    FILE_METADATA = "file-metadata"  # from an operator-entered label in the files
    USER_DECLARED = "user-declared"
    SYNTHETIC = "synthetic"  # profile of a simulated detector


class McaInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    firmware: str | None = None
    conversion_gain_channels: int | None = Field(default=None, gt=0)


class Geometry(BaseModel):
    """Measurement geometry. All fields optional: unknown stays unknown."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str | None = None
    source_detector_distance_mm: float | None = Field(default=None, ge=0.0)
    source_type: str | None = None  # e.g. "point", "disc", "Marinelli"
    absorbers: str | None = None

    @property
    def is_documented(self) -> bool:
        return self.source_detector_distance_mm is not None and self.source_type is not None


class _DetectorProfileBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    detector_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type_evidence: EvidenceLevel
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    crystal_diameter_mm: float | None = Field(default=None, gt=0.0)
    crystal_length_mm: float | None = Field(default=None, gt=0.0)
    mca: McaInfo = McaInfo()
    geometry: Geometry = Geometry()
    n_channels: int | None = Field(default=None, gt=0)
    notes: str = ""
    # Calibration history: append-only tuples, newest last.
    energy_calibrations: tuple[EnergyCalibration, ...] = ()
    resolution_calibrations: tuple[ResolutionCalibration, ...] = ()
    efficiency_calibrations: tuple[EfficiencyCalibration, ...] = ()

    # Plausible FWHM/E at 662 keV for this detector family (warning bounds only).
    PLAUSIBLE_RELATIVE_FWHM_662: ClassVar[tuple[float, float]]

    @model_validator(mode="after")
    def _calibrations_belong_here(self) -> Any:
        for cal in (
            *self.energy_calibrations,
            *self.resolution_calibrations,
            *self.efficiency_calibrations,
        ):
            if cal.detector_id is not None and cal.detector_id != self.detector_id:
                raise ValueError(
                    f"calibration for detector {cal.detector_id!r} stored in profile "
                    f"{self.detector_id!r}"
                )
        return self

    def resolution_is_plausible(self, relative_fwhm_at_662: float) -> bool:
        lo, hi = self.PLAUSIBLE_RELATIVE_FWHM_662
        return lo <= relative_fwhm_at_662 <= hi

    def missing_for_absolute_efficiency(self) -> tuple[str, ...]:
        """What must be documented before absolute efficiency work is permissible."""
        missing: list[str] = []
        if self.model is None:
            missing.append("detector model")
        if self.crystal_diameter_mm is None or self.crystal_length_mm is None:
            missing.append("crystal dimensions")
        if not self.geometry.is_documented:
            missing.append("source-detector geometry")
        return tuple(missing)


class HPGeProfile(_DetectorProfileBase):
    detector_type: Literal[DetectorType.HPGE] = DetectorType.HPGE
    bias_voltage_v: float | None = None
    relative_efficiency_percent: float | None = Field(default=None, gt=0.0)
    crystal_configuration: str | None = None  # e.g. "p-type coaxial"

    # ~0.1 % to ~0.6 % FWHM at 662 keV covers working HPGe systems generously.
    PLAUSIBLE_RELATIVE_FWHM_662: ClassVar[tuple[float, float]] = (0.001, 0.006)


class NaIProfile(_DetectorProfileBase):
    detector_type: Literal[DetectorType.NAI_TL] = DetectorType.NAI_TL
    pmt_voltage_v: float | None = None

    # ~5 % to ~12 % FWHM at 662 keV covers common NaI(Tl) scintillators.
    PLAUSIBLE_RELATIVE_FWHM_662: ClassVar[tuple[float, float]] = (0.05, 0.12)


DetectorProfile = Annotated[HPGeProfile | NaIProfile, Field(discriminator="detector_type")]
_PROFILE_ADAPTER: TypeAdapter[HPGeProfile | NaIProfile] = TypeAdapter(DetectorProfile)


def profile_from_dict(data: dict[str, Any]) -> HPGeProfile | NaIProfile:
    """Build the right profile class from stored data (YAML/JSON/SQLite row)."""
    return _PROFILE_ADAPTER.validate_python(data)
