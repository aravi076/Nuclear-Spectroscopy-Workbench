"""Exception hierarchy shared by all NSW layers."""

from __future__ import annotations


class NSWError(Exception):
    """Base class for all NSW errors."""


class UnitError(NSWError, ValueError):
    """Incompatible or unknown measurement units."""


class SpectrumValidationError(NSWError, ValueError):
    """A spectrum violates a structural or physical consistency rule."""


class CalibrationError(NSWError, ValueError):
    """A calibration record is internally inconsistent."""


class CalibrationNotVerifiedError(NSWError, RuntimeError):
    """An energy value was requested from a calibration that is not verified."""


class ProcessingError(NSWError, ValueError):
    """A requested processing step is not valid for the given inputs."""
