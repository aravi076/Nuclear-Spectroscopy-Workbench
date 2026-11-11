"""Numerical validation of the synthetic generator against analytical references.

Scope: this verifies that the generator realises its own stated mathematical model.
It says nothing about real detectors.

Tolerances
----------
* Deterministic expectations (bin integrals, moments) are compared at 1e-9 .. 1e-6:
  they are closed-form up to floating-point rounding and the Sheppard approximation.
* Sampled quantities are compared with their exact Poisson standard deviation; a
  5-sigma window on a fixed seed gives a false-failure probability below 1e-6 while
  still catching normalisation errors of a few percent at the count levels used.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from nsw.core import CalibrationOrigin, CalibrationStatus, DataOrigin, SpectrumKind
from nsw.detectors.profiles import DetectorType
from nsw.io import read_spectrum
from nsw.simulation import presets
from nsw.simulation.generator import (
    ELECTRON_REST_ENERGY_KEV,
    FWHM_PER_SIGMA,
    BackgroundModel,
    ResolutionModel,
    SyntheticLine,
    SyntheticSpec,
    background_expectation,
    compton_edge_kev,
    continuum_expectation,
    generate,
    peak_expectation,
    write_synthetic,
)


def hpge_spec(**kw: Any) -> SyntheticSpec:
    d: dict[str, Any] = dict(
        synthetic_id="t", live_time_s=1000.0, real_time_s=1010.0, seed=1, **presets.HPGE_BASE
    )
    d.update(kw)
    return SyntheticSpec(**d)


LINE = SyntheticLine(energy_kev=600.0, rate_cps=50.0)


# ---------------------------------------------------------------- model constants
def test_fwhm_sigma_constant() -> None:
    assert FWHM_PER_SIGMA == pytest.approx(2.354820045, rel=1e-9)  # 2*sqrt(2 ln 2)


def test_assumed_resolution_models_give_documented_values() -> None:
    h = presets.HPGE_BASE["resolution"]
    assert h.fwhm(100.0) == pytest.approx(1.0) and h.fwhm(1300.0) == pytest.approx(2.0)
    n = presets.NAI_BASE["resolution"]
    assert n.fwhm(662.0) / 662.0 == pytest.approx(0.070, abs=5e-4)


def test_compton_edge_formula() -> None:
    # E_C = 2E^2 / (m c^2 + 2E); evaluated independently here
    e = 1000.0
    assert compton_edge_kev(e) == pytest.approx(2 * e * e / (ELECTRON_REST_ENERGY_KEV + 2 * e))
    assert compton_edge_kev(e) < e


# ---------------------------------------------------------------- expectations
def test_peak_bin_integrals_conserve_area() -> None:
    mu = peak_expectation(hpge_spec(), LINE)
    assert mu.sum() == pytest.approx(50.0 * 1000.0, rel=1e-12)
    assert np.all(mu >= 0)


def test_peak_moments_match_gaussian_with_sheppard_correction() -> None:
    spec = hpge_spec()
    mu = peak_expectation(spec, LINE)
    ch = np.arange(1, spec.n_channels + 1, dtype=float)
    w = mu / mu.sum()
    mean = float((w * ch).sum())
    var = float((w * (ch - mean) ** 2).sum())
    sigma_ch = spec.resolution.sigma(600.0) / spec.energy_gain_kev_per_channel
    assert mean == pytest.approx(spec.channel_of(600.0), abs=1e-9)  # (600 - 0.5)/0.25 = 2398
    # variance of a unit-binned distribution = sigma^2 + 1/12 (Sheppard), sigma_ch ~ 2.5
    assert var == pytest.approx(sigma_ch**2 + 1.0 / 12.0, rel=1e-6)


def test_peak_maximum_equals_analytic_bin_integral() -> None:
    spec = hpge_spec()
    mu = peak_expectation(spec, LINE)
    sigma_ch = spec.resolution.sigma(600.0) / 0.25
    centre = round(spec.channel_of(600.0))  # 2398 exactly: peak centred on a channel
    expected = 50_000.0 * math.erf(0.5 / (sigma_ch * math.sqrt(2.0)))
    assert mu[centre - 1] == pytest.approx(expected, rel=1e-12)
    assert int(np.argmax(mu)) == centre - 1


def test_continuum_is_normalised_and_stops_near_compton_edge() -> None:
    spec = hpge_spec()
    line = SyntheticLine(energy_kev=1000.0, rate_cps=10.0, continuum_ratio=3.0)
    cont = continuum_expectation(spec, line)
    assert cont.sum() == pytest.approx(3.0 * 10.0 * 1000.0, rel=1e-12)
    edge_ch = spec.channel_of(compton_edge_kev(1000.0))
    sigma_ch = spec.resolution.sigma(compton_edge_kev(1000.0)) / 0.25
    above = cont[int(edge_ch + 6 * sigma_ch) :]
    assert above.sum() < 1e-6 * cont.sum()
    plateau = cont[100 : int(edge_ch - 6 * sigma_ch)]
    assert plateau.max() == pytest.approx(plateau.min(), rel=1e-9)  # flat shelf
    assert continuum_expectation(spec, LINE).sum() == 0.0  # ratio 0 -> no continuum


def test_background_expectation_scales_with_live_time() -> None:
    bg = BackgroundModel(constant_cps_per_channel=0.01, exp_amplitude_cps_per_channel=0.1)
    a = background_expectation(hpge_spec(background=bg, live_time_s=100.0, real_time_s=100.0))
    c = background_expectation(hpge_spec(background=bg, live_time_s=300.0, real_time_s=300.0))
    np.testing.assert_allclose(c, 3.0 * a, rtol=1e-12)
    # channel 1 centre is at 0.75 keV: rate = 0.01 + 0.1*exp(-0.75/300)
    assert a[0] == pytest.approx(100.0 * (0.01 + 0.1 * math.exp(-0.75 / 300.0)))
    assert a[-1] > 100.0 * 0.01  # tends to the constant term from above


# ---------------------------------------------------------------- sampling
def test_generation_is_deterministic_and_seed_dependent() -> None:
    spec = presets.hpge_scenarios()[0]
    a, b = generate(spec), generate(spec)
    np.testing.assert_array_equal(a.spectrum.counts, b.spectrum.counts)
    assert a.to_json() == b.to_json()
    other = generate(spec.model_copy(update={"seed": spec.seed + 1}))
    assert other.spectrum.data_sha256 != a.spectrum.data_sha256


def test_realised_counts_are_consistent_with_poisson_expectation() -> None:
    for spec in (*presets.hpge_scenarios(), *presets.nai_scenarios()):
        truth = generate(spec).truth
        for line in truth["lines"]:
            mu = line["expected_peak_counts_in_range"]
            assert abs(line["realised_peak_counts"] - mu) <= 5.0 * math.sqrt(mu) + 1.0
        mu = truth["total_expected_counts"]
        assert abs(truth["total_counts"] - mu) <= 5.0 * math.sqrt(mu)


def test_ensemble_mean_and_variance_are_poisson() -> None:
    base = hpge_spec(
        lines=(SyntheticLine(energy_kev=600.0, rate_cps=0.4),), live_time_s=100.0, real_time_s=100.0
    )  # mu = 40 counts
    n = 400
    areas = np.array(
        [
            generate(base.model_copy(update={"seed": s})).truth["lines"][0]["realised_peak_counts"]
            for s in range(n)
        ],
        dtype=float,
    )
    # mean: standard error sqrt(40/400) = 0.316 -> 5 sigma = 1.6
    assert areas.mean() == pytest.approx(40.0, abs=1.6)
    # variance/mean = 1 for Poisson; its sampling sd is ~sqrt(2/(n-1)) = 0.071 -> 5 sigma
    assert areas.var(ddof=1) / areas.mean() == pytest.approx(1.0, abs=0.36)


def test_channel_pulls_have_unit_width() -> None:
    result = generate(presets.hpge_scenarios()[4])  # high-count scenario
    mask = result.expected > 50.0
    pulls = (result.spectrum.counts[mask] - result.expected[mask]) / np.sqrt(result.expected[mask])
    assert mask.sum() > 1000
    assert pulls.mean() == pytest.approx(0.0, abs=5.0 / math.sqrt(mask.sum()))
    assert pulls.std(ddof=1) == pytest.approx(1.0, abs=0.1)


# ---------------------------------------------------------------- classification
def test_synthetic_spectra_are_unmistakably_synthetic() -> None:
    r = generate(presets.nai_scenarios()[0])
    s = r.spectrum
    assert s.origin is DataOrigin.SYNTHETIC and s.is_synthetic and s.source is None
    assert s.synthetic_id == "nai-isolated-peaks"
    assert s.energy_calibration is not None
    assert s.energy_calibration.origin is CalibrationOrigin.SYNTHETIC_TRUTH
    assert s.calibration_status is CalibrationStatus.VERIFIED
    assert any("SYNTHETIC" in w for w in s.warnings)
    assert any("not a real detector" in a for a in r.truth["assumptions"])
    np.testing.assert_allclose(s.energies()[:2], [3.0, 6.0])  # E = 3.0 * ch


def test_hpge_and_nai_scenarios_are_independent_models() -> None:
    hp, na = presets.synthetic_profiles()
    h, n = presets.hpge_scenarios()[0], presets.nai_scenarios()[0]
    assert (h.detector_type, n.detector_type) == (DetectorType.HPGE, DetectorType.NAI_TL)
    assert h.detector_id == hp.detector_id != n.detector_id == na.detector_id
    rh = h.resolution.fwhm(662.0) / 662.0
    rn = n.resolution.fwhm(662.0) / 662.0
    assert hp.resolution_is_plausible(rh) and not hp.resolution_is_plausible(rn)
    assert na.resolution_is_plausible(rn) and not na.resolution_is_plausible(rh)
    assert h.n_channels == 8192 and n.n_channels == 1024


def test_scenarios_cover_required_cases() -> None:
    for specs in (presets.hpge_scenarios(), presets.nai_scenarios()):
        by = {s.synthetic_id.split("-", 1)[1]: s for s in specs}
        assert set(by) == {
            "isolated-peaks",
            "isolated-peaks-no-continuum",
            "overlapping-doublet",
            "low-count",
            "high-count",
            "background-only",
        }
        d = by["overlapping-doublet"]
        sep = d.lines[1].energy_kev - d.lines[0].energy_kev
        assert sep < 2.5 * d.resolution.fwhm(d.lines[0].energy_kev)  # genuinely overlapping
        assert by["low-count"].lines[0].rate_cps * by["low-count"].live_time_s == 30.0
        assert by["high-count"].lines[0].rate_cps * by["high-count"].live_time_s == 2.0e6
        assert generate(by["background-only"]).spectrum.kind is SpectrumKind.BACKGROUND
        assert len({s.seed for s in specs}) == len(specs)


def test_invalid_specs_rejected() -> None:
    with pytest.raises(ValidationError):
        hpge_spec(live_time_s=100.0, real_time_s=50.0)
    with pytest.raises(ValidationError):
        hpge_spec(role="background", lines=(LINE,))
    with pytest.raises(ValidationError):
        hpge_spec(synthetic_id="has spaces")
    with pytest.raises(ValidationError):
        ResolutionModel(a=-1.0, b=0.0)
    with pytest.raises(ValueError, match="non-positive"):
        ResolutionModel(a=0.0, b=0.0).fwhm(100.0)


# ---------------------------------------------------------------- files
def test_write_read_round_trip(tmp_path: Path) -> None:
    result = generate(presets.hpge_scenarios()[2])
    path = write_synthetic(result, tmp_path / "hpge")
    assert path.name == "hpge-overlapping-doublet.nswsyn.json"
    s = read_spectrum(path)
    np.testing.assert_array_equal(s.counts, result.spectrum.counts)
    assert s.data_sha256 == result.truth["data_sha256"]
    assert s.origin is DataOrigin.SYNTHETIC and s.synthetic_id == result.spec.synthetic_id
    assert s.calibration_status is CalibrationStatus.VERIFIED
    assert (s.live_time_s, s.real_time_s) == (1000.0, 1020.0)
    assert s.detector_id == presets.HPGE_ID
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [ln["energy_kev"] for ln in doc["truth"]["lines"]] == [600.0, 603.0]
    assert doc["truth"]["seed"] == 1002


def test_background_role_survives_round_trip(tmp_path: Path) -> None:
    path = write_synthetic(generate(presets.nai_scenarios()[5]), tmp_path)
    assert read_spectrum(path).kind is SpectrumKind.BACKGROUND


def test_existing_file_is_never_overwritten_with_different_content(tmp_path: Path) -> None:
    spec = presets.hpge_scenarios()[3]
    first = write_synthetic(generate(spec), tmp_path)
    assert write_synthetic(generate(spec), tmp_path) == first  # identical: no-op
    with pytest.raises(FileExistsError):
        write_synthetic(generate(spec.model_copy(update={"seed": 5})), tmp_path)
