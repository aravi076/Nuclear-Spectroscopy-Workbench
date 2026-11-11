"""Calibration *records* (data only; fitting lives in ``nsw.analysis`` in later phases).

A calibration has an **origin** (where the numbers came from) and a **status**
(whether NSW may use it quantitatively). Vendor-embedded calibrations are always
``UNVERIFIED``: the one in the development dataset proved to be wrong.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from nsw.core.errors import CalibrationError, CalibrationNotVerifiedError


class CalibrationStatus(StrEnum):
    NONE = "none"  # no calibration available at all
    UNVERIFIED = "unverified"  # present, but must not be used quantitatively
    VERIFIED = "verified"  # validated against reference peaks (or exact synthetic truth)


class CalibrationOrigin(StrEnum):
    IMPORTED_VENDOR = "imported-vendor"  # read from an instrument file
    FITTED = "fitted"  # fitted by NSW from reference peaks
    ASSUMED = "assumed"  # typed in / nominal
    SYNTHETIC_TRUTH = "synthetic-truth"  # exact generator parameter of a synthetic spectrum


class CalibrationPoint(BaseModel):
    """One reference point of a calibration fit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel: float
    channel_uncertainty: float = Field(ge=0.0)
    reference_value: float
    reference_uncertainty: float = Field(ge=0.0)
    reference_source: str = Field(min_length=1)  # provenance of the reference value
    label: str = ""


class _CalibrationBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    coefficients: tuple[float, ...] = Field(min_length=1)
    covariance: tuple[tuple[float, ...], ...] | None = None
    origin: CalibrationOrigin
    status: CalibrationStatus = CalibrationStatus.UNVERIFIED
    detector_id: str | None = None
    method: str = ""
    created: datetime | None = None
    points: tuple[CalibrationPoint, ...] = ()
    note: str = ""

    @model_validator(mode="after")
    def _check_common(self) -> Any:
        if not all(np.isfinite(self.coefficients)):
            raise CalibrationError("calibration coefficients must be finite")
        if self.status is CalibrationStatus.NONE:
            raise CalibrationError("a calibration record cannot have status NONE")
        n = len(self.coefficients)
        if self.covariance is not None:
            cov = np.asarray(self.covariance, dtype=float)
            if cov.shape != (n, n):
                raise CalibrationError(f"covariance must be {n}x{n}, got {cov.shape}")
            if not np.allclose(cov, cov.T, rtol=1e-9, atol=0.0):
                raise CalibrationError("covariance must be symmetric")
            if np.any(np.diag(cov) < 0):
                raise CalibrationError("covariance diagonal must be non-negative")
        if self.status is CalibrationStatus.VERIFIED:
            if self.origin in (CalibrationOrigin.IMPORTED_VENDOR, CalibrationOrigin.ASSUMED):
                raise CalibrationError(
                    f"a calibration of origin {self.origin.value!r} cannot be VERIFIED; "
                    "only fitted calibrations and synthetic ground truth can"
                )
            if self.origin is CalibrationOrigin.FITTED:
                if len(self.points) <= n:
                    raise CalibrationError(
                        "a verified fitted calibration needs more reference points than "
                        "coefficients: without a redundant point it cannot be checked"
                    )
                if self.covariance is None:
                    raise CalibrationError("a verified fitted calibration needs a covariance")
        return self

    @property
    def is_verified(self) -> bool:
        return self.status is CalibrationStatus.VERIFIED


class EnergyCalibration(_CalibrationBase):
    """Polynomial channel-to-energy relation, E[keV] = sum_i c_i * channel**i.

    Channels are 1-based, matching the instrument (TECHNICAL_DECISIONS TD-003).
    """

    energy_unit: str = "keV"

    @model_validator(mode="after")
    def _check_energy(self) -> Any:
        if self.energy_unit != "keV":
            raise CalibrationError("energy calibrations are stored in keV")
        if len(self.coefficients) < 2:
            raise CalibrationError("an energy calibration needs at least offset and slope")
        return self

    def energy_at(
        self, channel: ArrayLike, *, allow_unverified: bool = False
    ) -> NDArray[np.float64]:
        """Energy in keV at the given 1-based channel position(s).

        Raises :class:`CalibrationNotVerifiedError` unless the calibration is verified.
        ``allow_unverified=True`` exists solely so that an untrusted calibration can be
        *displayed as metadata*; results obtained that way must never feed an analysis.
        """
        if not self.is_verified and not allow_unverified:
            raise CalibrationNotVerifiedError(
                f"energy calibration ({self.origin.value}) is {self.status.value}; "
                "energies must not be used for quantitative analysis"
            )
        ch = np.asarray(channel, dtype=np.float64)
        # polyval wants highest order first
        return np.asarray(np.polyval(self.coefficients[::-1], ch), dtype=np.float64)

    @property
    def channel_range(self) -> tuple[float, float] | None:
        """Channel span of the reference points: the range in which the calibration is
        supported by data. None for calibrations without points (vendor, synthetic truth)."""
        if not self.points:
            return None
        channels = [p.channel for p in self.points]
        return min(channels), max(channels)

    def is_extrapolated(self, channel: ArrayLike) -> NDArray[np.bool_]:
        """True where ``channel`` lies outside the span of the reference points."""
        ch = np.asarray(channel, dtype=np.float64)
        span = self.channel_range
        if span is None:
            return np.zeros(ch.shape, dtype=np.bool_)
        return np.asarray((ch < span[0]) | (ch > span[1]), dtype=np.bool_)

    def gain_at(self, channel: ArrayLike) -> NDArray[np.float64]:
        """dE/dchannel in keV per channel (no verification needed: used for propagation)."""
        ch = np.asarray(channel, dtype=np.float64)
        derivative = [i * c for i, c in enumerate(self.coefficients)][1:]
        return np.asarray(np.polyval(derivative[::-1], ch), dtype=np.float64)

    def energy_uncertainty_at(self, channel: ArrayLike) -> NDArray[np.float64]:
        """Standard uncertainty of the calibrated energy from the coefficient covariance,
        u^2 = g^T C g with g = (1, ch, ch^2, ...). Excludes the uncertainty of the channel
        position itself. Raises if no covariance is stored."""
        if self.covariance is None:
            raise CalibrationError("this calibration has no covariance matrix")
        ch = np.atleast_1d(np.asarray(channel, dtype=np.float64))
        powers = np.vstack([ch**i for i in range(len(self.coefficients))])
        cov = np.asarray(self.covariance, dtype=np.float64)
        var = np.einsum("ik,ij,jk->k", powers, cov, powers)
        out = np.sqrt(np.maximum(var, 0.0))
        return np.asarray(out.reshape(np.shape(channel)), dtype=np.float64)


class ResolutionCalibration(_CalibrationBase):
    """Parameters of a named FWHM(E) model; evaluated by ``nsw.analysis`` (later phase)."""

    model: str = Field(min_length=1)  # e.g. "sqrt_quadratic": FWHM = sqrt(a + bE + cE^2)
    fwhm_unit: str = "keV"


class EfficiencyCalibration(_CalibrationBase):
    """Parameters of a named efficiency model. ``kind`` distinguishes what is described."""

    model: str = Field(min_length=1)
    kind: str = Field(pattern="^(relative|absolute-full-energy-peak)$")
    geometry_description: str = ""
