"""Calibration record tests."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from nsw.core import (
    CalibrationError,
    CalibrationNotVerifiedError,
    CalibrationOrigin,
    CalibrationPoint,
    CalibrationStatus,
    EfficiencyCalibration,
    EnergyCalibration,
    ResolutionCalibration,
)

PT = CalibrationPoint(
    channel=10.0,
    channel_uncertainty=0.1,
    reference_value=5.0,
    reference_uncertainty=0.01,
    reference_source="unit-test",
)


def test_default_status_is_unverified() -> None:
    cal = EnergyCalibration(coefficients=(0.0, 1.0), origin=CalibrationOrigin.ASSUMED)
    assert cal.status is CalibrationStatus.UNVERIFIED and not cal.is_verified


@pytest.mark.parametrize("origin", [CalibrationOrigin.IMPORTED_VENDOR, CalibrationOrigin.ASSUMED])
def test_vendor_and_assumed_can_never_be_verified(origin: CalibrationOrigin) -> None:
    with pytest.raises(ValidationError, match="cannot be VERIFIED"):
        EnergyCalibration(coefficients=(0.0, 1.0), origin=origin, status=CalibrationStatus.VERIFIED)


def test_verified_fit_needs_points_and_covariance() -> None:
    kw: dict[str, Any] = dict(
        coefficients=(0.0, 1.0),
        origin=CalibrationOrigin.FITTED,
        status=CalibrationStatus.VERIFIED,
    )
    cov = ((1.0, 0.0), (0.0, 1.0))
    with pytest.raises(ValidationError, match="reference"):
        EnergyCalibration(**kw, covariance=cov, points=(PT,))
    # two points fix a straight line exactly: nothing left to check it against
    with pytest.raises(ValidationError, match="redundant"):
        EnergyCalibration(**kw, covariance=cov, points=(PT, PT))
    with pytest.raises(ValidationError, match="covariance"):
        EnergyCalibration(**kw, points=(PT, PT, PT))
    assert EnergyCalibration(**kw, covariance=cov, points=(PT, PT, PT)).is_verified


def test_range_gain_and_energy_uncertainty() -> None:
    pts = tuple(
        PT.model_copy(update={"channel": c, "reference_value": 0.5 * c}) for c in (10, 50, 90)
    )
    cal = EnergyCalibration(
        coefficients=(1.0, 0.5, 1e-4),
        covariance=((4.0, 0.0, 0.0), (0.0, 1e-4, 0.0), (0.0, 0.0, 1e-10)),
        origin=CalibrationOrigin.FITTED,
        points=pts,
    )
    assert cal.channel_range == (10.0, 90.0)
    assert list(cal.is_extrapolated([5.0, 10.0, 50.0, 90.5])) == [True, False, False, True]
    # dE/dch = 0.5 + 2e-4 ch
    assert cal.gain_at(100.0) == pytest.approx(0.52)
    # u^2 = 4 + 1e-4 ch^2 + 1e-10 ch^4 at ch = 100: 4 + 1 + 0.01
    assert cal.energy_uncertainty_at(100.0) == pytest.approx(5.01**0.5)
    vendor = EnergyCalibration(coefficients=(0.0, 1.0), origin=CalibrationOrigin.IMPORTED_VENDOR)
    assert vendor.channel_range is None and not vendor.is_extrapolated(1e6)
    with pytest.raises(CalibrationError, match="no covariance"):
        vendor.energy_uncertainty_at(10.0)


def test_unverified_energy_requires_explicit_opt_in() -> None:
    cal = EnergyCalibration(
        coefficients=(-1.5, 0.25, 0.0), origin=CalibrationOrigin.IMPORTED_VENDOR
    )
    with pytest.raises(CalibrationNotVerifiedError):
        cal.energy_at(4)
    # display-only path: E = -1.5 + 0.25 * 4 = -0.5
    assert cal.energy_at(4, allow_unverified=True) == pytest.approx(-0.5)


def test_quadratic_polynomial_order() -> None:
    cal = EnergyCalibration(
        coefficients=(1.0, 2.0, 3.0),
        origin=CalibrationOrigin.SYNTHETIC_TRUTH,
        status=CalibrationStatus.VERIFIED,
    )
    np.testing.assert_allclose(cal.energy_at([0, 1, 2]), [1.0, 6.0, 17.0])  # 1 + 2x + 3x^2


@pytest.mark.parametrize(
    "kw",
    [
        dict(coefficients=(1.0,)),  # needs offset and slope
        dict(coefficients=(0.0, float("nan"))),
        dict(coefficients=(0.0, 1.0), covariance=((1.0,),)),  # wrong shape
        dict(coefficients=(0.0, 1.0), covariance=((1.0, 0.5), (0.1, 1.0))),  # asymmetric
        dict(coefficients=(0.0, 1.0), covariance=((-1.0, 0.0), (0.0, 1.0))),  # negative variance
        dict(coefficients=(0.0, 1.0), status=CalibrationStatus.NONE),
        dict(coefficients=(0.0, 1.0), energy_unit="MeV"),
        dict(coefficients=(0.0, 1.0), unknown_field=1),
    ],
)
def test_invalid_energy_calibrations_rejected(kw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EnergyCalibration(origin=CalibrationOrigin.ASSUMED, **kw)


def test_records_are_frozen() -> None:
    cal = EnergyCalibration(coefficients=(0.0, 1.0), origin=CalibrationOrigin.ASSUMED)
    with pytest.raises(ValidationError):
        cal.status = CalibrationStatus.VERIFIED  # type: ignore[misc]


def test_resolution_and_efficiency_records() -> None:
    r = ResolutionCalibration(
        coefficients=(0.5, 1e-3, 0.0), origin=CalibrationOrigin.ASSUMED, model="sqrt_quadratic"
    )
    assert r.fwhm_unit == "keV"
    e = EfficiencyCalibration(
        coefficients=(1.0,), origin=CalibrationOrigin.ASSUMED, model="log-poly", kind="relative"
    )
    assert e.kind == "relative"
    with pytest.raises(ValidationError):
        EfficiencyCalibration(
            coefficients=(1.0,),
            origin=CalibrationOrigin.ASSUMED,
            model="log-poly",
            kind="intrinsic-guess",
        )
