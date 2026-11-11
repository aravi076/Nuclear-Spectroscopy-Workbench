"""Detector profile and settings tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nsw.config import ConfigurationError, Settings, load_settings
from nsw.core import CalibrationOrigin, EnergyCalibration, ResolutionCalibration
from nsw.detectors.profiles import (
    DetectorType,
    EvidenceLevel,
    Geometry,
    HPGeProfile,
    NaIProfile,
    profile_from_dict,
)


def hpge(**kw: Any) -> HPGeProfile:
    return HPGeProfile(
        detector_id="hpge-1", name="Lab HPGe", type_evidence=EvidenceLevel.MEASURED, **kw
    )


def nai(**kw: Any) -> NaIProfile:
    return NaIProfile(detector_id="nai-1", name="NaI", type_evidence=EvidenceLevel.SYNTHETIC, **kw)


def test_profiles_are_distinct_types() -> None:
    h, n = hpge(), nai()
    assert h.detector_type is DetectorType.HPGE and n.detector_type is DetectorType.NAI_TL
    assert not hasattr(h, "pmt_voltage_v") and not hasattr(n, "bias_voltage_v")
    assert HPGeProfile.PLAUSIBLE_RELATIVE_FWHM_662 != NaIProfile.PLAUSIBLE_RELATIVE_FWHM_662


def test_resolution_plausibility_separates_detector_families() -> None:
    h, n = hpge(), nai()
    assert h.resolution_is_plausible(0.002) and not n.resolution_is_plausible(0.002)
    assert n.resolution_is_plausible(0.07) and not h.resolution_is_plausible(0.07)


def test_round_trip_selects_correct_class() -> None:
    n = nai(pmt_voltage_v=800.0)
    restored = profile_from_dict(n.model_dump(mode="json"))
    assert isinstance(restored, NaIProfile) and restored == n
    with pytest.raises(ValidationError):
        profile_from_dict(
            {
                "detector_type": "plastic",
                "detector_id": "x",
                "name": "x",
                "type_evidence": "measured",
            }
        )


def test_unknown_parameters_stay_unknown() -> None:
    h = hpge()
    assert h.model is None and h.crystal_diameter_mm is None
    assert h.missing_for_absolute_efficiency() == (
        "detector model",
        "crystal dimensions",
        "source-detector geometry",
    )
    full = hpge(
        model="X",
        crystal_diameter_mm=60.0,
        crystal_length_mm=60.0,
        geometry=Geometry(source_detector_distance_mm=100.0, source_type="point"),
    )
    assert full.missing_for_absolute_efficiency() == ()


def test_calibration_of_another_detector_rejected() -> None:
    other = EnergyCalibration(
        coefficients=(0.0, 1.0), origin=CalibrationOrigin.ASSUMED, detector_id="someone-else"
    )
    with pytest.raises(ValidationError, match="someone-else"):
        hpge(energy_calibrations=(other,))
    own = ResolutionCalibration(
        coefficients=(1.0,), origin=CalibrationOrigin.ASSUMED, model="m", detector_id="hpge-1"
    )
    assert hpge(resolution_calibrations=(own,)).resolution_calibrations == (own,)


def test_invalid_profile_values() -> None:
    with pytest.raises(ValidationError):
        hpge(crystal_diameter_mm=-1.0)
    with pytest.raises(ValidationError):
        hpge(pmt_voltage_v=800.0)  # NaI-only field


# ---- settings --------------------------------------------------------------------
def test_settings_load_and_root_check(tmp_path: Path) -> None:
    exp = tmp_path / "exp"
    exp.mkdir()
    cfg = tmp_path / "local.yaml"
    ws = (tmp_path / "ws").as_posix()
    cfg.write_text(
        f'paths:\n  experimental_roots: ["{exp.as_posix()}"]\n  workspace: "{ws}"\n',
        encoding="utf-8",
    )
    s = load_settings(cfg)
    assert isinstance(s, Settings)
    assert s.is_inside_experimental_root(exp / "sub" / "a.csv")
    assert not s.is_inside_experimental_root(tmp_path / "ws" / "a.csv")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "- a\n- b\n",
        "paths: {}\n",
        'paths:\n  workspace: ""\n',
        "paths:\n  workspace: w\n  extra: 1\n",
        "paths: [unclosed\n",
    ],
)
def test_bad_settings_give_configuration_error(tmp_path: Path, text: str) -> None:
    cfg = tmp_path / "local.yaml"
    cfg.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(cfg)


def test_missing_settings_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_settings(tmp_path / "nope.yaml")
