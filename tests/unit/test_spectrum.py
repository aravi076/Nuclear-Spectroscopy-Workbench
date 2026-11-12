"""Spectrum data-model tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from nsw.core import (
    CalibrationNotVerifiedError,
    CalibrationOrigin,
    CalibrationPoint,
    CalibrationStatus,
    DataOrigin,
    EnergyCalibration,
    MeasurementStatus,
    ProcessingStep,
    SourceFile,
    Spectrum,
    SpectrumKind,
    SpectrumValidationError,
)
from tests.conftest import FAKE_SHA, make_raw, make_source

VENDOR = EnergyCalibration(coefficients=(-1.5, 0.15), origin=CalibrationOrigin.IMPORTED_VENDOR)
STEP = ProcessingStep("test-derivation", {"alpha": 2.0}, parents=("abc",))


def make_net(
    values: Any = (1.0, -2.0, 3.0), variance: Any = (1.0, 4.0, 9.0), **kw: Any
) -> Spectrum:
    d: dict[str, Any] = dict(
        kind=SpectrumKind.NET, origin=DataOrigin.EXPERIMENTAL, provenance=(STEP,)
    )
    d.update(kw)
    return Spectrum(counts=np.asarray(values), variance=np.asarray(variance), **d)


# ---- array dimensions / channel consistency -------------------------------------
def test_channels_are_one_based_and_consistent() -> None:
    s = make_raw(np.arange(8192))
    assert s.n_channels == 8192
    assert s.channels[0] == 1 and s.channels[-1] == 8192
    assert s.channels.size == s.counts.size


@pytest.mark.parametrize("bad", [np.zeros((4, 2)), np.array([]), np.array(5)])
def test_wrong_dimensions_rejected(bad: Any) -> None:
    with pytest.raises(SpectrumValidationError):
        make_raw(bad)


def test_variance_length_must_match() -> None:
    with pytest.raises(SpectrumValidationError, match="does not match"):
        make_net(variance=(1.0, 2.0))


# ---- count validation ------------------------------------------------------------
def test_negative_raw_counts_rejected_with_channel() -> None:
    with pytest.raises(SpectrumValidationError, match="channel 3"):
        make_raw([1, 2, -1, 4])


def test_fractional_raw_counts_rejected() -> None:
    with pytest.raises(SpectrumValidationError, match="whole numbers"):
        make_raw([1.0, 2.5])


def test_whole_float_counts_become_int64() -> None:
    s = make_raw([1.0, 2.0, 3.0])
    assert s.counts.dtype == np.int64


@pytest.mark.parametrize("bad", [[1.0, np.nan], [np.inf, 1.0]])
def test_non_finite_rejected(bad: list[float]) -> None:
    with pytest.raises(SpectrumValidationError):
        make_raw(bad)
    with pytest.raises(SpectrumValidationError):
        make_net(values=bad, variance=(1.0, 1.0))


def test_non_numeric_rejected() -> None:
    with pytest.raises(SpectrumValidationError, match="numeric"):
        make_raw(np.array(["a", "b"]))


def test_net_keeps_negative_values() -> None:
    s = make_net()
    assert s.counts[1] == -2.0  # negative net estimates are preserved, never clipped
    assert s.counts.dtype == np.float64


def test_net_requires_variance_and_provenance() -> None:
    with pytest.raises(SpectrumValidationError, match="variance"):
        Spectrum(
            counts=np.ones(3),
            kind=SpectrumKind.NET,
            origin=DataOrigin.EXPERIMENTAL,
            provenance=(STEP,),
        )
    with pytest.raises(SpectrumValidationError, match="provenance"):
        make_net(provenance=())
    with pytest.raises(SpectrumValidationError, match="non-negative"):
        make_net(variance=(1.0, -1.0, 1.0))


def test_count_data_must_not_carry_explicit_variance() -> None:
    with pytest.raises(SpectrumValidationError, match="Poisson"):
        make_raw([1, 2], variance=np.array([1.0, 2.0]))


def test_poisson_variance_equals_counts() -> None:
    s = make_raw([0, 4, 100])
    np.testing.assert_array_equal(s.variance_array(), [0.0, 4.0, 100.0])
    np.testing.assert_allclose(s.uncertainty_array(), [0.0, 2.0, 10.0])
    assert s.total == 104


# ---- timing ----------------------------------------------------------------------
@pytest.mark.parametrize("field", ["live_time_s", "real_time_s"])
@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_times_rejected(field: str, bad: float) -> None:
    with pytest.raises(SpectrumValidationError):
        make_raw(**{field: bad})


def test_live_time_cannot_exceed_real_time() -> None:
    with pytest.raises(SpectrumValidationError, match="exceeds real time"):
        make_raw(live_time_s=100.0, real_time_s=99.0)
    # allowed when the recorded times are known to be rounded to whole seconds
    make_raw(live_time_s=100.0, real_time_s=99.6, time_quantum_s=1.0)


def test_dead_time_fraction() -> None:
    assert make_raw(live_time_s=80.0, real_time_s=100.0).dead_time_fraction == pytest.approx(0.2)
    assert make_raw(real_time_s=None).dead_time_fraction is None


# ---- missing metadata ------------------------------------------------------------
def test_missing_metadata_is_reported_not_invented() -> None:
    s = make_raw(live_time_s=None, real_time_s=None)
    assert s.missing_metadata() == (
        "live time",
        "real time",
        "acquisition start time",
        "detector identifier",
        "sample name",
        "verified energy calibration",
    )
    full = make_raw(start_time=datetime(2026, 3, 10, 9, 15, 27), detector_id="d1", sample_name="x")
    assert full.missing_metadata() == ("verified energy calibration",)


def test_start_time_local_drops_zone_only() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    s = make_raw(start_time=datetime(2026, 3, 10, 9, 15, 27, tzinfo=ist))
    assert s.start_time_local == datetime(2026, 3, 10, 9, 15, 27)


# ---- immutability ----------------------------------------------------------------
def test_counts_array_is_read_only_and_detached_from_input() -> None:
    original = np.array([1, 2, 3], dtype=np.int64)
    s = make_raw(original)
    with pytest.raises(ValueError, match="read-only"):
        s.counts[0] = 99
    original[0] = 99  # mutating the caller's array must not leak into the spectrum
    assert s.counts[0] == 1
    assert s.verify_integrity()


def test_attributes_cannot_be_reassigned() -> None:
    s = make_raw()
    with pytest.raises(AttributeError):
        s.live_time_s = 5.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        s.metadata["x"] = "y"  # type: ignore[index]


def test_tampering_is_detected_by_hash() -> None:
    s = make_raw([1, 2, 3])
    s.counts.setflags(write=True)  # deliberate circumvention
    s.counts[0] = 7
    assert not s.verify_integrity()


def test_evolve_cannot_touch_channel_data() -> None:
    s = make_raw()
    for field in ("counts", "kind", "origin", "variance", "first_channel"):
        with pytest.raises(SpectrumValidationError, match="evolve"):
            s.evolve(**{field: None})
    s2 = s.evolve(measurement_status=MeasurementStatus.INDEPENDENT)
    assert s2.data_sha256 == s.data_sha256
    assert s.measurement_status is MeasurementStatus.UNCLASSIFIED  # original untouched


def test_data_hash_depends_only_on_channel_data() -> None:
    a = make_raw([1, 2, 3], source=make_source("a.csv"), live_time_s=10.0, real_time_s=11.0)
    b = make_raw([1, 2, 3], source=make_source("b.ava"))
    c = make_raw([1, 2, 4])
    assert a.data_sha256 == b.data_sha256 != c.data_sha256


# ---- kinds -----------------------------------------------------------------------
def test_background_designation_creates_new_object_with_history() -> None:
    raw = make_raw()
    bg = raw.designated_as_background("analyst choice")
    assert raw.kind is SpectrumKind.RAW and bg.kind is SpectrumKind.BACKGROUND
    np.testing.assert_array_equal(raw.counts, bg.counts)
    assert bg.provenance[-1].operation == "designate-background"
    assert bg.provenance[-1].parents == (raw.data_sha256,)
    assert bg.designated_as_background() is bg
    with pytest.raises(SpectrumValidationError):
        make_net().designated_as_background()


def test_kind_and_origin_must_be_enums() -> None:
    with pytest.raises(SpectrumValidationError):
        make_raw(kind="raw")
    with pytest.raises(SpectrumValidationError):
        make_raw(origin="experimental")


# ---- experimental vs synthetic ---------------------------------------------------
def test_experimental_count_data_requires_file_provenance() -> None:
    with pytest.raises(SpectrumValidationError, match="file provenance"):
        make_raw(source=None)


def test_synthetic_requires_id_and_experimental_forbids_it() -> None:
    with pytest.raises(SpectrumValidationError, match="synthetic_id"):
        make_raw(origin=DataOrigin.SYNTHETIC, source=None)
    with pytest.raises(SpectrumValidationError, match="synthetic_id"):
        make_raw(synthetic_id="syn-1")
    s = make_raw(origin=DataOrigin.SYNTHETIC, source=None, synthetic_id="syn-1")
    assert s.is_synthetic and "synthetic" in repr(s)
    assert not make_raw().is_synthetic


def test_source_file_validation() -> None:
    with pytest.raises(SpectrumValidationError):
        SourceFile("p", "f", 1, "xyz", "fmt", "A")
    with pytest.raises(SpectrumValidationError):
        SourceFile("p", "", 1, FAKE_SHA, "fmt", "A")


# ---- calibration status ----------------------------------------------------------
def test_no_calibration_means_status_none_and_no_energies() -> None:
    s = make_raw()
    assert s.calibration_status is CalibrationStatus.NONE
    with pytest.raises(CalibrationNotVerifiedError):
        s.energies()


def test_vendor_calibration_is_unverified_and_unusable() -> None:
    s = make_raw(embedded_calibration=VENDOR)
    assert s.calibration_status is CalibrationStatus.UNVERIFIED
    with pytest.raises(CalibrationNotVerifiedError):
        s.energies()
    with pytest.raises(SpectrumValidationError, match="embedded"):
        make_raw(energy_calibration=VENDOR)  # cannot be promoted into the usable slot


def test_verified_fitted_calibration_gives_energies() -> None:
    pts = tuple(
        CalibrationPoint(
            channel=c,
            channel_uncertainty=0.1,
            reference_value=e,
            reference_uncertainty=0.01,
            reference_source="unit-test",
        )
        for c, e in ((100.0, 50.0), (200.0, 100.0), (300.0, 150.0))
    )
    cal = EnergyCalibration(
        coefficients=(0.0, 0.5),
        covariance=((1e-4, 0.0), (0.0, 1e-8)),
        origin=CalibrationOrigin.FITTED,
        status=CalibrationStatus.VERIFIED,
        points=pts,
    )
    s = make_raw([1, 2, 3, 4], energy_calibration=cal, embedded_calibration=VENDOR)
    assert s.calibration_status is CalibrationStatus.VERIFIED
    np.testing.assert_allclose(s.energies(), [0.5, 1.0, 1.5, 2.0])  # E = 0.5 * channel


def test_synthetic_truth_calibration_only_on_synthetic_data() -> None:
    truth = EnergyCalibration(
        coefficients=(0.0, 1.0),
        origin=CalibrationOrigin.SYNTHETIC_TRUTH,
        status=CalibrationStatus.VERIFIED,
    )
    with pytest.raises(SpectrumValidationError, match="synthetic-truth"):
        make_raw(energy_calibration=truth)
    s = make_raw(
        origin=DataOrigin.SYNTHETIC, source=None, synthetic_id="s", energy_calibration=truth
    )
    assert s.calibration_status is CalibrationStatus.VERIFIED
