"""Regression tests for the findings of the independent review of Phases 8 and 9
(2026-11-11). Each test reproduces one finding; numbers in comments are hand values.

All nuclides are fictitious; nothing is nuclear data and nothing concerns measurements.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from nsw.analysis.activity import line_activity
from nsw.analysis.efficiency import (
    EfficiencyCurve,
    EfficiencyPoint,
    GammaLine,
    MeasurementTiming,
    fit_efficiency_curve,
    relative_points,
)
from nsw.analysis.identification import (
    Confidence,
    IdentificationSettings,
    ObservedPeak,
    identify,
)
from nsw.core import ProcessingError
from nsw.core.nuclear_data import GammaRecord, IntensityConvention, NuclideRecord, ReferenceLibrary
from nsw.io import reference_data as rd
from nsw.services import calibration as cs
from nsw.services import identification as ids
from nsw.simulation import sources
from nsw.simulation.reference import FICTITIOUS
from tests.conftest import make_raw
from tests.unit.test_identification_service import real_like_library, spectrum_and_fits

MODEL = sources.SYNTHETIC_EFFICIENCY
PD, REL = IntensityConvention.PER_DECAY, IntensityConvention.RELATIVE
WIDE = IdentificationSettings(energy_range_kev=(50.0, 3000.0))


def curve() -> EfficiencyCurve:
    points = [
        EfficiencyPoint(ln.energy_kev, MODEL.efficiency(ln.energy_kev), 0.01, 0.0, 0.0, "A")
        for ln in sources.SYN_CAL.lines
    ]
    return fit_efficiency_curve(points, order=2, geometry="synthetic")


def rec(name: str, lines: list[tuple[float, float] | tuple[float, float, IntensityConvention]]):
    return NuclideRecord(
        nuclide=name,
        provenance=FICTITIOUS,
        lines=tuple(
            GammaRecord(
                energy_kev=ln[0],
                energy_unc_kev=0.01,
                intensity=ln[1],
                intensity_unc=0.01 * ln[1],
                convention=ln[2] if len(ln) > 2 else PD,  # type: ignore[misc]
            )
            for ln in lines
        ),
    )


def pk(energy: float, p: float, scale: float = 2e7, shift: float = 0.0) -> ObservedPeak:
    area = MODEL.efficiency(energy) * p * scale
    return ObservedPeak(energy + shift, 0.02, 0.03, area, math.sqrt(area))


# ---------------------------------------------------------------- identification rules
def test_a_rejected_candidate_does_not_subsume_a_good_one() -> None:
    x = rec("DECOY-X", [(300.0, 0.4), (800.0, 0.3)])
    y = rec("DECOY-Y", [(300.0, 0.1), (800.0, 0.1), (500.0, 0.1), (1000.0, 0.9)])
    peaks = [pk(300.0, 0.4), pk(800.0, 0.3), ObservedPeak(500.0, 0.02, 0.03, 400.0, 40.0)]
    result = identify(peaks, [x, y], WIDE, efficiency=curve(), detectable_area=lambda e: 50.0)
    assert result.candidate("DECOY-Y").confidence is Confidence.NOT_SUPPORTED
    assert result.candidate("DECOY-X").confidence is Confidence.CONSISTENT
    assert result.candidates[0].nuclide == "DECOY-X"


def test_matched_line_outside_the_efficiency_curve_carries_no_weight() -> None:
    z = rec("DECOY-Z", [(500.0, 0.5), (600.0, 0.4), (700.0, 0.4), (800.0, 0.4), (2500.0, 0.5)])
    base = [pk(500.0, 0.5)]
    extra = [*base, ObservedPeak(2500.0, 0.02, 0.03, 500.0, 30.0)]
    kw = dict(efficiency=curve(), detectable_area=lambda e: 50.0)
    without = identify(base, [z], WIDE, **kw).candidates[0]  # type: ignore[arg-type]
    with_far = identify(extra, [z], WIDE, **kw).candidates[0]  # type: ignore[arg-type]
    assert without.confidence is Confidence.NOT_SUPPORTED
    # the 2500 keV match cannot be weighed (curve ends at 1800 keV): same line fraction
    assert with_far.line_fraction == pytest.approx(without.line_fraction)
    assert with_far.confidence is Confidence.NOT_SUPPORTED


def test_relative_lines_do_not_lift_a_single_absolute_line_above_possible() -> None:
    m = rec("DECOY-M", [(300.0, 0.4), (800.0, 100.0, REL), (1000.0, 500.0, REL)])
    peaks = [pk(300.0, 0.4), pk(800.0, 0.3)]
    for kw in ({}, {"efficiency": curve()}):
        c = identify(peaks, [m], WIDE, **kw).candidates[0]  # type: ignore[arg-type]
        assert c.confidence is Confidence.POSSIBLE
        assert "one peak" in c.reasons[0]


def test_unresolved_lines_on_the_anchor_peak_share_its_area() -> None:
    d = rec("DECOY-D", [(500.00, 0.30), (500.05, 0.30), (900.0, 0.012)])
    scale = 2e5
    area = MODEL.efficiency(500.0) * 0.6 * scale  # both lines in one peak
    peak = ObservedPeak(500.02, 0.02, 0.03, area, math.sqrt(area))
    true_900 = MODEL.efficiency(900.0) * 0.012 * scale
    result = identify(
        [peak], [d], WIDE, efficiency=curve(), detectable_area=lambda e: true_900 / 1.5
    )
    (u,) = result.candidates[0].unmatched
    assert u.predicted_area == pytest.approx(true_900, rel=0.01)
    assert not u.expected  # 1.5 x the limit, below the factor 2 required


def test_two_entries_for_one_unresolvable_peak_count_once() -> None:
    e = rec("DECOY-E", [(500.00, 0.5), (500.06, 0.4)])
    twice = [
        ObservedPeak(500.00, 0.02, 0.03, 1e4, 100.0),
        ObservedPeak(500.06, 0.02, 0.03, 1e4, 100.0),
    ]
    c = identify(twice, [e]).candidates[0]
    assert c.confidence is Confidence.POSSIBLE  # 0.06 keV apart with 0.036 keV uncertainty
    # peaks that are clearly separate still count separately
    f = rec("DECOY-F", [(500.0, 0.5), (503.0, 0.4)])
    apart = [pk(500.0, 0.5), pk(503.0, 0.4)]
    assert identify(apart, [f]).candidates[0].confidence is Confidence.PROBABLE


def test_missing_intensity_uncertainty_is_reported() -> None:
    lines = tuple(
        GammaRecord(energy_kev=e, energy_unc_kev=0.01, intensity=p, convention=PD)
        for e, p in ((300.0, 0.4), (800.0, 0.3))
    )
    g = NuclideRecord(nuclide="DECOY-G", provenance=FICTITIOUS, lines=lines)
    c = identify([pk(300.0, 0.4), pk(800.0, 0.3)], [g], WIDE, efficiency=curve()).candidates[0]
    assert any("no uncertainty for some emission probabilities" in w for w in c.warnings)


def test_uniqueness_ignores_relative_intensities() -> None:
    f1 = rec("DECOY-F", [(300.0, 0.9), (800.0, 100.0, REL)])
    f2 = rec("DECOY-H", [(800.0, 0.5)])
    result = identify([pk(300.0, 0.9), pk(800.0, 0.5)], [f1, f2], WIDE)
    # the only absolute line of DECOY-F is unshared
    assert result.candidate("DECOY-F").uniqueness == pytest.approx(1.0)


def test_result_says_that_missing_means_no_fitted_peak_supplied() -> None:
    result = identify([pk(300.0, 0.4)], [rec("DECOY-X", [(300.0, 0.4)])])
    assert any("only against the peaks supplied" in w for w in result.warnings)


# ---------------------------------------------------------------- efficiency / activity
def test_relative_groups_with_separate_normalisation_are_refused() -> None:
    def group(energies: tuple[float, float], label: str) -> list[EfficiencyPoint]:
        lines = [GammaLine(e, 0.5, 0.0, "invented") for e in energies]
        areas = [(MODEL.efficiency(e) * 0.5 * 1e6, 100.0) for e in energies]
        return relative_points(areas, lines, label)

    a, b = group((100.0, 300.0), "nuclide A"), group((600.0, 1200.0), "nuclide B")
    with pytest.raises(ProcessingError, match="separately normalised"):
        fit_efficiency_curve(a + b, 1)
    assert fit_efficiency_curve(a, 1).ndf == 0  # one group alone is fine


def test_singular_covariance_is_a_processing_error() -> None:
    points = [
        EfficiencyPoint(e, MODEL.efficiency(e), 0.0, 0.0, 0.03, "A") for e in (100.0, 500.0, 1500.0)
    ]
    with pytest.raises(ProcessingError, match="singular"):
        fit_efficiency_curve(points, 1)


def test_non_detections_have_a_reportable_quantity() -> None:
    line = GammaLine(500.0, 0.5, 0.005, "invented")
    timing = MeasurementTiming(sources.REFERENCE_TIME, 1000.0, 1000.0)
    # -5 +- 10 counts: activity -5/(eps P t), statistical uncertainty 10/(eps P t)
    per_count = 1.0 / (MODEL.efficiency(500.0) * 0.5 * 1000.0)
    q = line_activity(-5.0, 10.0, line, curve(), timing, 1e15).quantity(0.0)
    assert q.value == pytest.approx(-5.0 * per_count, rel=1e-6)
    assert q.stat == pytest.approx(10.0 * per_count, rel=1e-6) and q.sys >= 0.0
    zero = line_activity(0.0, 10.0, line, curve(), timing, 1e15).quantity(0.0)
    assert zero.value == 0.0 and zero.stat == pytest.approx(10.0 * per_count, rel=1e-6)


# ---------------------------------------------------------------- gates
def test_calibration_verified_on_synthetic_data_cannot_unlock_a_measurement() -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_CAL, live=20000.0, seed=11)
    rows = cs.peak_rows(fits)
    points = [
        cs.make_point(r, ln.energy_kev, 0.01, "synthetic ground truth")
        for r, ln in zip(rows, sources.SYN_CAL.lines, strict=True)
    ]
    record = cs.make_record(spectrum, cs.calibrate(points, 1), datetime(2026, 11, 11))
    assert record.is_verified
    measured = make_raw(np.asarray(spectrum.counts))
    with pytest.raises(ProcessingError, match="experimental spectrum"):
        cs.apply_calibration(measured, record)
    # even if such a spectrum existed, identification stays closed for measurements
    forced = measured.evolve(energy_calibration=record)
    missing = ids.missing_inputs(forced, real_like_library())
    assert any("not enabled for experimental spectra" in m for m in missing)
    with pytest.raises(ProcessingError, match="not available"):
        ids.identify(forced, fits, real_like_library())


def test_fits_must_belong_to_the_spectrum() -> None:
    a, _ = spectrum_and_fits(sources.SYN_X, seed=7)
    _, fits_b = spectrum_and_fits(sources.SYN_X, seed=8)
    with pytest.raises(ProcessingError, match="different spectrum"):
        ids.identify(a, fits_b, ids.fictitious_library())


def test_default_range_keeps_the_outermost_calibration_lines() -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_CAL, live=20000.0, seed=11)
    rows = cs.peak_rows(fits)
    points = [
        cs.make_point(r, ln.energy_kev, 0.01, "synthetic ground truth")
        for r, ln in zip(rows, sources.SYN_CAL.lines, strict=True)
    ]
    record = cs.make_record(spectrum, cs.calibrate(points, 1), datetime(2026, 11, 11))
    fitted = cs.apply_calibration(spectrum, record)
    result = ids.identify(fitted, fits, ids.fictitious_library())
    syn_cal = result.candidate("SYN-CAL")
    assert len(syn_cal.matched_peaks) == 8 and result.unexplained_peaks == ()


# ---------------------------------------------------------------- data model
def test_evaluation_names_are_normalised_and_half_lives_finite(tmp_path: Path) -> None:
    doc = rd.library_document(real_like_library())
    doc["records"][0]["provenance"]["evaluation"] = " ddep "
    path = tmp_path / "lib.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    (record,) = rd.JsonFileProvider(path).load()
    assert record.provenance.evaluation == "DDEP"
    lib = ReferenceLibrary(name="x", records=(record,))
    assert lib.select(("DDEP",)) == (record,)
    with pytest.raises(ValueError, match="more than one record"):
        ReferenceLibrary(name="dup", records=(record, real_like_library().records[0]))
    doc["records"][0]["half_life_s"] = float("inf")
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(rd.ReferenceDataError, match="finite"):
        rd.JsonFileProvider(path).load()
