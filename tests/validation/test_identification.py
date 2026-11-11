"""Validation of the nuclide identification framework on fictitious nuclides.

Every nuclide here is invented (``nsw.simulation.reference``): SYN-CAL and SYN-X are the
generator's test sources, the DECOY records are placed to provoke wrong answers. Nothing
is nuclear data and nothing is computed for the laboratory spectra.

References: hand calculations (in comments), a closed form for the two-line intensity
test, and the synthetic ground truth.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from nsw.analysis.efficiency import EfficiencyCurve, EfficiencyPoint, fit_efficiency_curve
from nsw.analysis.identification import (
    Confidence,
    IdentificationSettings,
    ObservedPeak,
    detection_limit_counts,
    identify,
)
from nsw.core import ProcessingError
from nsw.core.nuclear_data import GammaRecord, IntensityConvention, NuclideRecord
from nsw.simulation import sources
from nsw.simulation.reference import FICTITIOUS, fictitious_library

MODEL = sources.SYNTHETIC_EFFICIENCY
RECORDS = fictitious_library().select(("FICTITIOUS",))
U_PEAK, U_CAL = 0.02, 0.03  # keV; with u_line = 0.01: sigma = sqrt(0.0014) = 0.03742 keV
SIGMA = math.sqrt(U_PEAK**2 + 0.01**2 + U_CAL**2)


def curve(rel: float = 0.01) -> EfficiencyCurve:
    points = [
        EfficiencyPoint(ln.energy_kev, MODEL.efficiency(ln.energy_kev), rel, 0.0, 0.0, "A")
        for ln in sources.SYN_CAL.lines
    ]
    return fit_efficiency_curve(points, order=2, geometry="synthetic")


def peaks_of(
    *srcs: sources.SyntheticSource,
    scale: float = 2e7,
    shift: float = 0.0,
    skip: tuple[float, ...] = (),
) -> list[ObservedPeak]:
    """Noise-free peaks: area = eps(E) * P * scale, Poisson uncertainty."""
    out = []
    for src in srcs:
        for ln in src.lines:
            if ln.energy_kev in skip:
                continue
            area = MODEL.efficiency(ln.energy_kev) * ln.emission_probability * scale
            out.append(ObservedPeak(ln.energy_kev + shift, U_PEAK, U_CAL, area, math.sqrt(area)))
    return sorted(out, key=lambda p: p.energy_kev)


def by_name(result: object) -> dict[str, object]:
    return {c.nuclide: c for c in result.candidates}  # type: ignore[attr-defined]


# ---------------------------------------------------------------- matching window
def one_line(energy: float = 500.0, unc: float | None = 0.01) -> NuclideRecord:
    return NuclideRecord(
        nuclide="DECOY-Z",
        provenance=FICTITIOUS,
        lines=(
            GammaRecord(
                energy_kev=energy,
                energy_unc_kev=unc,
                intensity=0.5,
                convention=IntensityConvention.PER_DECAY,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("delta", "k", "u_cal", "matched"),
    [
        (0.110, 3.0, U_CAL, True),  # 3 sigma = 0.1122 keV
        (-0.110, 3.0, U_CAL, True),
        (0.115, 3.0, U_CAL, False),
        (0.070, 2.0, U_CAL, True),  # 2 sigma = 0.0748 keV
        (0.080, 2.0, U_CAL, False),
        (0.080, 3.0, 0.0, False),  # without u_cal: sigma = 0.02236, 3 sigma = 0.0671
        (0.060, 3.0, 0.0, True),
        (0.080, 3.0, 0.10, True),  # a less certain calibration widens the window
    ],
)
def test_energy_window_hand_values(delta: float, k: float, u_cal: float, matched: bool) -> None:
    peak = ObservedPeak(500.0 + delta, U_PEAK, u_cal, 1000.0, 40.0)
    result = identify([peak], [one_line()], IdentificationSettings(k=k))
    assert bool(result.candidates) is matched
    if matched:
        m = result.candidates[0].matches[0]
        sigma = math.sqrt(U_PEAK**2 + 0.01**2 + u_cal**2)
        assert m.delta_kev == pytest.approx(delta) and m.sigma_kev == pytest.approx(sigma)
        assert m.z == pytest.approx(delta / sigma)
        assert result.unexplained_peaks == ()
    else:
        assert result.unexplained_peaks == (0,)
        assert result.not_evaluated == (("DECOY-Z", "no line matches a peak"),)


def test_extra_uncertainty_and_missing_line_uncertainty() -> None:
    peak = ObservedPeak(500.2, U_PEAK, U_CAL, 1000.0, 40.0)
    assert not identify([peak], [one_line()]).candidates
    wide = identify([peak], [one_line()], IdentificationSettings(extra_energy_unc_kev=0.1))
    assert wide.candidates[0].matches[0].sigma_kev == pytest.approx(math.hypot(SIGMA, 0.1))
    # a library line without energy uncertainty: taken as 0 and said so
    exact = identify([ObservedPeak(500.05, U_PEAK, U_CAL, 1e3, 40.0)], [one_line(unc=None)])
    c = exact.candidates[0]
    assert c.matches[0].sigma_kev == pytest.approx(math.hypot(U_PEAK, U_CAL))
    assert any("no uncertainty" in w for w in c.warnings)
    # the nearest peak in units of sigma is taken when two lie in the window
    two = [
        ObservedPeak(499.93, U_PEAK, U_CAL, 1e3, 40.0),
        ObservedPeak(500.03, U_PEAK, U_CAL, 1e3, 40.0),
    ]
    assert identify(two, [one_line()]).candidates[0].matches[0].peak_index == 1


# ---------------------------------------------------------------- single source
def test_correct_nuclide_first_and_single_coincidences_never_accepted() -> None:
    result = identify(peaks_of(sources.SYN_X), RECORDS)
    names = [c.nuclide for c in result.candidates]
    assert names[0] == "SYN-X"
    c = by_name(result)
    syn_x = c["SYN-X"]
    assert syn_x.confidence is Confidence.PROBABLE  # no efficiency curve: never more
    assert len(syn_x.matched_peaks) == 3 and not syn_x.missing and syn_x.intensity is None
    assert syn_x.line_fraction == 1.0
    # DECOY-A: its 650 keV line coincides, its stronger 950 keV line is absent
    a = c["DECOY-A"]
    assert a.confidence is Confidence.NOT_SUPPORTED
    assert [u.line.energy_kev for u in a.missing] == [950.0]
    assert a.line_fraction == pytest.approx(0.6 / 1.4)
    # DECOY-B: one line only, coincident: possible at most
    b = c["DECOY-B"]
    assert b.confidence is Confidence.POSSIBLE and "only one peak" in b.reasons[0]
    assert set(names) == {"SYN-X", "DECOY-A", "DECOY-B"}
    assert {n for n, _ in result.not_evaluated} == {"SYN-CAL", "DECOY-C", "DECOY-D", "DECOY-E"}
    # interference is reported on both sides
    shared = {m.line.energy_kev: m.shared_with for m in syn_x.matches}
    assert shared == {200.0: (), 650.0: ("DECOY-A",), 1250.0: ("DECOY-B",)}
    assert syn_x.uniqueness == pytest.approx((0.5 + 0.5 * 0.5) / 1.0)
    assert result.unexplained_peaks == ()
    assert any("not identifications" in w for w in result.warnings)
    assert any("no efficiency curve" in w for w in result.warnings)


def test_single_line_nuclide_is_never_more_than_possible_even_with_efficiency() -> None:
    peak = peaks_of(sources.SYN_X)[2:]  # only 1250 keV present
    result = identify(peak, [r for r in RECORDS if r.nuclide == "DECOY-B"], efficiency=curve())
    (c,) = result.candidates
    assert c.confidence is Confidence.POSSIBLE and c.intensity is None and c.score == 1.0


def test_efficiency_curve_enables_the_intensity_test() -> None:
    result = identify(peaks_of(sources.SYN_X), RECORDS, efficiency=curve())
    c = by_name(result)
    syn_x = c["SYN-X"]
    assert syn_x.confidence is Confidence.CONSISTENT
    assert syn_x.intensity.ndf == 2 and syn_x.intensity.chi2 == pytest.approx(0.0, abs=1e-6)
    assert result.with_efficiency and not any("no efficiency" in w for w in result.warnings)
    assert any("coincidence summing" in w for w in syn_x.warnings)
    # areas that do not follow the emission probabilities: same energies, wrong ratios
    peaks = peaks_of(sources.SYN_X)
    peaks[1] = replace(peaks[1], area=3.0 * peaks[1].area)
    off = by_name(identify(peaks, RECORDS, efficiency=curve()))["SYN-X"]
    assert off.confidence is Confidence.POSSIBLE and not off.intensity.consistent
    assert off.score == pytest.approx(0.5 * off.line_fraction * off.uniqueness)
    assert "do not follow the emission probabilities" in off.reasons[0]


def test_two_line_intensity_test_matches_closed_form() -> None:
    # chi2 = (y1 - y2)^2 / (v11 + v22 - 2 v12) for two values with covariance v
    cv = curve(0.02)
    rec = NuclideRecord(
        nuclide="DECOY-Y",
        provenance=FICTITIOUS,
        lines=tuple(
            GammaRecord(
                energy_kev=e,
                energy_unc_kev=0.01,
                intensity=p,
                intensity_unc=0.02 * p,
                convention=IntensityConvention.PER_DECAY,
            )
            for e, p in ((300.0, 0.4), (1100.0, 0.25))
        ),
    )
    a1 = MODEL.efficiency(300.0) * 0.4 * 1e6
    a2 = MODEL.efficiency(1100.0) * 0.25 * 1e6 * 1.10  # 10 % off
    peaks = [
        ObservedPeak(300.0, U_PEAK, U_CAL, a1, 0.03 * a1),
        ObservedPeak(1100.0, U_PEAK, U_CAL, a2, 0.04 * a2),
    ]
    test = identify(peaks, [rec], efficiency=cv).candidates[0].intensity
    eff = cv.efficiency([300.0, 1100.0])[0]
    y = np.log(np.array([a1, a2]) / (np.array([0.4, 0.25]) * eff))
    v = cv.relative_covariance([300.0, 1100.0])
    var = (0.03**2 + 0.02**2) + (0.04**2 + 0.02**2) + v[0, 0] + v[1, 1] - 2 * v[0, 1]
    assert test.ndf == 1 and test.chi2 == pytest.approx((y[0] - y[1]) ** 2 / var, rel=1e-9)
    assert test.consistent  # 10 % against about 6 % combined uncertainty


# ---------------------------------------------------------------- interference
def test_decoy_with_two_coincident_lines_needs_the_efficiency_curve() -> None:
    peaks = peaks_of(sources.SYN_CAL)
    plain = by_name(identify(peaks, RECORDS))
    assert next(iter(plain)) == "SYN-CAL" and plain["SYN-CAL"].confidence is Confidence.PROBABLE
    assert len(plain["SYN-CAL"].matched_peaks) == 8
    # DECOY-C matches 300 and 800 keV, misses nothing: only the parsimony rule holds it back
    dc = plain["DECOY-C"]
    assert dc.confidence is Confidence.POSSIBLE and not dc.missing
    assert any("also explained by SYN-CAL" in r for r in dc.reasons)
    # DECOY-D: relative intensities, energies only
    dd = plain["DECOY-D"]
    assert dd.confidence is Confidence.POSSIBLE and math.isnan(dd.line_fraction)
    assert any("relative intensities" in w for w in dd.warnings)

    with_curve = by_name(identify(peaks, RECORDS, efficiency=curve()))
    assert with_curve["SYN-CAL"].confidence is Confidence.CONSISTENT
    dc = with_curve["DECOY-C"]
    assert dc.intensity is not None and not dc.intensity.consistent
    assert dc.score < 0.5 * with_curve["SYN-CAL"].score
    assert with_curve["DECOY-D"].intensity is None  # relative lines are never tested


def test_mixture_and_unexplained_peaks() -> None:
    peaks = peaks_of(sources.SYN_CAL, sources.SYN_X)
    peaks.append(ObservedPeak(1600.0, U_PEAK, U_CAL, 500.0, 30.0))
    result = identify(peaks, RECORDS, efficiency=curve())
    # both sources lead; their order follows the share of unshared lines (0.75 vs 0.70)
    assert [c.nuclide for c in result.candidates[:2]] == ["SYN-X", "SYN-CAL"]
    assert result.candidates[1].uniqueness == pytest.approx((0.8 + 0.5 * 1.2) / 2.0)
    assert all(c.confidence is Confidence.CONSISTENT for c in result.candidates[:2])
    assert [result.peaks[i].energy_kev for i in result.unexplained_peaks] == [1600.0]


# ---------------------------------------------------------------- missing lines
def test_missing_strong_line_lowers_class_and_score() -> None:
    full = by_name(identify(peaks_of(sources.SYN_CAL), RECORDS))["SYN-CAL"]
    # without the strongest line (300 keV, P = 0.40)
    cut = by_name(identify(peaks_of(sources.SYN_CAL, skip=(300.0,)), RECORDS))["SYN-CAL"]
    assert [u.line.energy_kev for u in cut.missing] == [300.0]
    assert cut.confidence is Confidence.NOT_SUPPORTED and cut.score < full.score
    assert cut.line_fraction == pytest.approx(1.6 / 2.0)  # sum P matched / (matched + missing)
    # without a weak line (1800 keV, P = 0.10 < 0.5 * 0.40): not expected without a curve
    wide = IdentificationSettings(energy_range_kev=(50.0, 2000.0))
    weak = by_name(identify(peaks_of(sources.SYN_CAL, skip=(1800.0,)), RECORDS, wide))["SYN-CAL"]
    assert weak.confidence is Confidence.PROBABLE and not weak.missing
    assert [u.line.energy_kev for u in weak.unmatched] == [1800.0]
    # with the curve the predicted area decides: 1800 keV would be far above the limit
    judged = by_name(
        identify(
            peaks_of(sources.SYN_CAL, skip=(1800.0,)),
            RECORDS,
            wide,
            efficiency=curve(),
            detectable_area=lambda e: 50.0,
        )
    )["SYN-CAL"]
    (missing,) = judged.missing
    anchor = MODEL.efficiency(80.0) * 0.30 * 2e7  # most significant peak
    expected = anchor * (MODEL.efficiency(1800.0) * 0.10) / (MODEL.efficiency(80.0) * 0.30)
    assert missing.predicted_area == pytest.approx(expected, rel=1e-6)
    assert missing.detectable_area == 50.0 and judged.confidence is Confidence.POSSIBLE
    # the same line is not missing when it could not have been seen
    faint = by_name(
        identify(
            peaks_of(sources.SYN_CAL, skip=(1800.0,)),
            RECORDS,
            wide,
            efficiency=curve(),
            detectable_area=lambda e: 1e9,
        )
    )["SYN-CAL"]
    assert not faint.missing and faint.confidence is Confidence.CONSISTENT


def test_lines_outside_range_or_curve_are_not_judged() -> None:
    peaks = peaks_of(sources.SYN_X, skip=(1250.0,))
    limited = identify(peaks, RECORDS, IdentificationSettings(energy_range_kev=(100.0, 1000.0)))
    syn_x = by_name(limited)["SYN-X"]
    assert not syn_x.unmatched and syn_x.confidence is Confidence.PROBABLE
    assert "DECOY-B" in {n for n, _ in limited.not_evaluated}
    # default range: the span of the peaks, and the result says so
    auto = identify(peaks, RECORDS)
    assert auto.energy_range_kev[1] < 1250.0
    assert any("span of the fitted peaks" in w for w in auto.warnings)
    # a line beyond the efficiency curve (80-1800 keV) is neither missing nor tested
    rec = NuclideRecord(
        nuclide="DECOY-W",
        provenance=FICTITIOUS,
        lines=tuple(
            GammaRecord(energy_kev=e, intensity=0.5, convention=IntensityConvention.PER_DECAY)
            for e in (200.0, 650.0, 2500.0)
        ),
    )
    result = identify(
        peaks,
        [rec],
        IdentificationSettings(energy_range_kev=(50.0, 3000.0)),
        efficiency=curve(),
    )
    (u,) = result.candidates[0].unmatched
    assert not u.expected and "outside the efficiency curve" in u.reason


# ---------------------------------------------------------------- safety
def test_invalid_inputs_and_conflicting_libraries() -> None:
    peaks = peaks_of(sources.SYN_X)
    with pytest.raises(ProcessingError, match="no peaks"):
        identify([], RECORDS)
    with pytest.raises(ProcessingError, match="library is empty"):
        identify(peaks, [])
    with pytest.raises(ProcessingError, match="more than one record per nuclide"):
        identify(peaks, [RECORDS[0], RECORDS[0]])
    for bad in (
        dict(k=0.0),
        dict(strong_ratio=0.0),
        dict(missing_factor=0.5),
        dict(energy_range_kev=(10.0, 5.0)),
        dict(extra_energy_unc_kev=-1.0),
    ):
        with pytest.raises(ProcessingError):
            IdentificationSettings(**bad)  # type: ignore[arg-type]
    with pytest.raises(ProcessingError):
        ObservedPeak(100.0, 0.01, 0.01, 10.0, 0.0)
    # a record with nothing usable in range is listed, not silently dropped
    result = identify(peaks, RECORDS, IdentificationSettings(min_emission_probability=0.95))
    assert not result.candidates and len(result.not_evaluated) == len(RECORDS)


def test_detection_limit_hand_value() -> None:
    # 1.645^2 + 2 * 1.645 * sqrt(2 * 100) = 2.706 + 46.53
    assert detection_limit_counts(100.0) == pytest.approx(49.23, abs=0.01)
    assert detection_limit_counts(0.0) == pytest.approx(2.706, abs=0.001)
    with pytest.raises(ProcessingError):
        detection_limit_counts(-1.0)
