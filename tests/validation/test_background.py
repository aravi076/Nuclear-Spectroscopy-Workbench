"""Validation of background subtraction and background comparison.

Reference values are derived by hand (shown in comments) or from the exact statistical
properties of independently generated synthetic spectra.

Tolerances
----------
* Arithmetic identities: exact or 1e-12 relative (pure floating-point arithmetic).
* Pull distributions over N independent values: mean within 5/sqrt(N), standard
  deviation within 5/sqrt(2N) of the ideal 0 and 1 (five standard errors).
* Hypothesis tests on fixed seeds: thresholds chosen so that a correct implementation
  fails with probability < 1e-3, while the alternative tested differs by many sigma.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pytest

from nsw.analysis.background import (
    ASSUMPTION,
    block_sums,
    check_compatibility,
    compare_count_rates,
    same_acquisition,
    smooth_for_display,
    subtract_background,
)
from nsw.core import (
    DataOrigin,
    MeasurementStatus,
    ProcessingError,
    Spectrum,
    SpectrumKind,
    Unit,
)
from nsw.simulation import presets
from nsw.simulation.generator import (
    BackgroundModel,
    SyntheticSpec,
    background_expectation,
    generate,
)
from tests.conftest import make_raw, make_source

T_A = datetime(2026, 3, 9, 11, 2, 41)
T_B = datetime(2026, 3, 10, 15, 47, 6)


def gross(counts: Any = (100, 4, 0, 50), **kw: Any) -> Spectrum:
    d: dict[str, Any] = dict(
        live_time_s=200.0, real_time_s=210.0, start_time=T_B, source=make_source("gross.csv")
    )
    d.update(kw)
    return make_raw(counts, **d)


def background(counts: Any = (40, 10, 2, 0), **kw: Any) -> Spectrum:
    d: dict[str, Any] = dict(
        live_time_s=400.0, real_time_s=401.0, start_time=T_A, source=make_source("bg.csv")
    )
    d.update(kw)
    return make_raw(counts, **d).designated_as_background()


# ---------------------------------------------------------------- arithmetic
def test_net_and_variance_match_hand_calculation() -> None:
    r = subtract_background(gross(), background())
    assert r.alpha == 0.5  # 200 s / 400 s
    # net = G - 0.5*B ; var = G + 0.25*B
    np.testing.assert_array_equal(r.net.counts, [80.0, -1.0, -1.0, 50.0])
    np.testing.assert_array_equal(r.net.variance_array(), [110.0, 6.5, 0.5, 50.0])
    np.testing.assert_allclose(r.net.uncertainty_array(), np.sqrt([110.0, 6.5, 0.5, 50.0]))


def test_negative_net_values_are_preserved() -> None:
    r = subtract_background(gross([0, 1, 2]), background([10, 10, 10]))
    np.testing.assert_array_equal(r.net.counts, [-5.0, -4.0, -3.0])
    assert r.net.counts.min() < 0


def test_live_time_not_real_time_sets_the_scaling() -> None:
    # same live times, very different real times -> alpha unchanged
    a = subtract_background(gross(real_time_s=210.0), background(real_time_s=401.0))
    b = subtract_background(gross(real_time_s=390.0), background(real_time_s=800.0))
    assert a.alpha == b.alpha == 0.5
    np.testing.assert_array_equal(a.net.counts, b.net.counts)


def test_inputs_are_untouched_and_result_is_a_distinct_net_spectrum() -> None:
    g, b = gross(), background()
    hashes = (g.data_sha256, b.data_sha256)
    r = subtract_background(g, b)
    assert (
        (g.data_sha256, b.data_sha256) == hashes and g.verify_integrity() and b.verify_integrity()
    )
    assert g.kind is SpectrumKind.RAW and b.kind is SpectrumKind.BACKGROUND
    net = r.net
    assert net.kind is SpectrumKind.NET and net.origin is DataOrigin.EXPERIMENTAL
    assert net.measurement_status is MeasurementStatus.DERIVED
    assert net.counts.dtype == np.float64
    assert (net.live_time_s, net.real_time_s) == (200.0, 210.0)
    assert net.source is g.source


def test_processing_history_makes_the_background_traceable() -> None:
    r = subtract_background(gross(), background())
    step = r.net.provenance[-1]
    assert step.operation == "background-subtraction"
    assert step.parents == (r.gross.data_sha256, r.background.data_sha256)
    p = step.parameters
    assert p["alpha"] == 0.5 and p["background"] == "bg.csv" and p["gross"] == "gross.csv"
    assert (p["gross_live_time_s"], p["background_live_time_s"]) == (200.0, 400.0)
    assert (p["gross_total_counts"], p["background_total_counts"]) == (154, 52)
    assert p["assumption"] == ASSUMPTION and ASSUMPTION in r.net.warnings


def test_region_sum_with_uncertainties() -> None:
    r = subtract_background(gross(), background())
    q = r.region(1, 2)
    # net: 80 + (-1) = 79 ; var: 110 + 6.5 = 116.5 ; no rounding -> sys = 0
    assert q.unit is Unit.COUNT and q.value == 79.0
    assert q.stat == pytest.approx(math.sqrt(116.5)) and q.sys == 0.0
    assert r.region(1, 4).value == 128.0
    for bad in ((0, 2), (3, 2), (1, 5)):
        with pytest.raises(ProcessingError):
            r.region(*bad)


def test_rounded_live_times_give_a_separate_correlated_uncertainty() -> None:
    g = gross(live_time_s=200.0, real_time_s=210.0, time_quantum_s=1.0)
    b = background(live_time_s=400.0, real_time_s=401.0, time_quantum_s=1.0)
    r = subtract_background(g, b)
    # uniform rounding error: sd = 1/sqrt(12) s on each time
    rel = math.hypot((1 / math.sqrt(12)) / 200.0, (1 / math.sqrt(12)) / 400.0)
    assert r.alpha_sys == pytest.approx(0.5 * rel)
    np.testing.assert_array_equal(r.net.variance_array(), [110.0, 6.5, 0.5, 50.0])  # unchanged
    q = r.region(1, 4)
    assert q.sys == pytest.approx(r.alpha_sys * 52.0)  # alpha_sys * sum(B)
    assert sum("rounded" in w for w in r.compatibility.warnings) == 2


# ---------------------------------------------------------------- invalid combinations
@pytest.mark.parametrize(
    ("g_kw", "b_kw", "message"),
    [
        ({}, {"counts": (1, 2, 3)}, "channel ranges differ"),
        ({"live_time_s": None, "real_time_s": None}, {}, "gross spectrum has no live time"),
        ({}, {"live_time_s": None, "real_time_s": None}, "background spectrum has no live time"),
        ({"start_time": T_A}, {}, "same acquisition"),
        ({"detector_id": "det-1"}, {"detector_id": "det-2"}, "different detectors"),
        (
            {"metadata": {"[154] Coarse Gain": "2"}},
            {"metadata": {"[154] Coarse Gain": "4"}},
            "hardware settings differ",
        ),
    ],
)
def test_invalid_combinations_are_rejected(
    g_kw: dict[str, Any], b_kw: dict[str, Any], message: str
) -> None:
    with pytest.raises(ProcessingError, match=message):
        subtract_background(gross(**g_kw), background(**b_kw))


def test_undesignated_background_is_rejected() -> None:
    undesignated = make_raw((40, 10, 2, 0), start_time=T_A)
    with pytest.raises(ProcessingError, match="not been designated"):
        subtract_background(gross(), undesignated)
    with pytest.raises(ProcessingError, match="itself designated"):
        subtract_background(background(start_time=T_B), background())


def test_spectrum_cannot_be_its_own_background() -> None:
    g = gross(start_time=None)
    twin = make_raw(g.counts, start_time=None).designated_as_background()
    assert same_acquisition(g, twin)  # identical data, even without timestamps
    with pytest.raises(ProcessingError, match="same acquisition"):
        subtract_background(g, twin)


def test_intermediate_save_cannot_serve_as_background() -> None:
    b = background().evolve(measurement_status=MeasurementStatus.INTERMEDIATE_SAVE)
    with pytest.raises(ProcessingError, match="intermediate save"):
        subtract_background(gross(), b)


def test_experimental_and_synthetic_data_cannot_be_mixed() -> None:
    syn = generate(presets.hpge_scenarios()[5]).spectrum
    g = gross(np.zeros(syn.n_channels, dtype=np.int64))
    with pytest.raises(ProcessingError, match="experimental data with synthetic data"):
        subtract_background(g, syn)


def test_net_spectrum_cannot_be_subtracted_again() -> None:
    net = subtract_background(gross(), background()).net
    with pytest.raises(ProcessingError, match="not measured counts"):
        subtract_background(net, background())


def test_all_reasons_are_reported_together() -> None:
    c = check_compatibility(gross(live_time_s=None, real_time_s=None), make_raw((1, 2, 3)))
    assert not c.ok and len(c.errors) >= 3


def test_warnings_for_unverifiable_assumptions() -> None:
    c = check_compatibility(gross(), background())
    assert c.ok
    assert any("detector identity is not recorded" in w for w in c.warnings)
    assert any("hardware gain settings are not recorded" in w for w in c.warnings)
    meta = {"[154] Coarse Gain": "2", "[70] Conversion Gain": "8192"}
    c2 = check_compatibility(
        gross(detector_id="d", metadata=meta), background(detector_id="d", metadata=meta)
    )
    assert c2.ok and c2.warnings == ()
    hot = gross(live_time_s=200.0, real_time_s=260.0)  # 23 % dead time
    assert any("pile-up" in w for w in check_compatibility(hot, background()).warnings)


# ---------------------------------------------------------------- statistical behaviour
BG_MODEL = BackgroundModel(constant_cps_per_channel=0.05, exp_amplitude_cps_per_channel=0.5)


def bg_spec(
    seed: int, live: float, model: BackgroundModel = BG_MODEL, n: int = 2048
) -> SyntheticSpec:
    base = {**presets.HPGE_BASE, "n_channels": n}
    return SyntheticSpec(
        synthetic_id=f"bg-{seed}",
        live_time_s=live,
        real_time_s=live,
        seed=seed,
        role="background",
        background=model,
        **base,
    )


def as_gross(spec: SyntheticSpec) -> Spectrum:
    """A background-only spectrum generated in the source role (i.e. a 'blank sample')."""
    return generate(spec.model_copy(update={"role": "source"})).spectrum


def test_net_of_two_background_measurements_is_zero_within_uncertainty() -> None:
    g = as_gross(bg_spec(11, 1000.0))
    b = generate(bg_spec(12, 4000.0)).spectrum
    r = subtract_background(g, b)
    pulls = r.net.counts / r.net.uncertainty_array()
    n = pulls.size  # every channel has > 50 expected counts, so the pulls are ~normal
    assert pulls.mean() == pytest.approx(0.0, abs=5.0 / math.sqrt(n))
    assert pulls.std(ddof=1) == pytest.approx(1.0, abs=5.0 / math.sqrt(2 * n))
    total = r.region(1, n)
    assert abs(total.value) < 5.0 * total.stat


def test_variance_estimate_matches_exact_poisson_variance() -> None:
    spec_g, spec_b = bg_spec(21, 1000.0), bg_spec(22, 4000.0)
    r = subtract_background(as_gross(spec_g), generate(spec_b).spectrum)
    # exact: var = mu_G + alpha^2 mu_B with the generator's expectations
    exact = background_expectation(spec_g) + r.alpha**2 * background_expectation(spec_b)
    ratio = r.net.variance_array().sum() / exact.sum()
    assert ratio == pytest.approx(1.0, abs=5.0 / math.sqrt(exact.sum()))


def test_source_counts_are_recovered_after_subtraction() -> None:
    specs = {s.synthetic_id: s for s in presets.hpge_scenarios()}
    src = generate(specs["hpge-isolated-peaks"])
    bg = generate(specs["hpge-background-only"])
    r = subtract_background(src.spectrum, bg.spectrum)
    assert r.alpha == pytest.approx(0.2)  # 1000 s / 5000 s
    expected_source = sum(
        ln["expected_peak_counts_in_range"] + ln["expected_continuum_counts"]
        for ln in src.truth["lines"]
    )
    total = r.region(1, src.spectrum.n_channels)
    assert abs(total.value - expected_source) < 5.0 * total.stat
    assert total.stat / expected_source < 0.01  # the test is sensitive at the percent level


def test_ensemble_coverage_of_the_region_uncertainty() -> None:
    pulls = []
    for seed in range(300):
        g = as_gross(bg_spec(1000 + seed, 50.0, n=256))
        b = generate(bg_spec(5000 + seed, 150.0, n=256)).spectrum
        q = subtract_background(g, b).region(1, 256)
        pulls.append(q.value / q.stat)
    arr = np.array(pulls)
    assert arr.mean() == pytest.approx(0.0, abs=5.0 / math.sqrt(300))
    assert arr.std(ddof=1) == pytest.approx(1.0, abs=5.0 / math.sqrt(600))
    inside = np.mean(np.abs(arr) < 1.0)  # 68.3 % expected; binomial sd = 2.7 %
    assert inside == pytest.approx(0.683, abs=0.135)


# ---------------------------------------------------------------- comparison
def test_block_sums() -> None:
    np.testing.assert_array_equal(block_sums(np.arange(10), 4), [6, 22])  # 0..3, 4..7
    with pytest.raises(ProcessingError):
        block_sums(np.arange(3), 4)
    with pytest.raises(ProcessingError):
        block_sums(np.arange(3), 0)


def test_comparison_statistic_matches_hand_calculation() -> None:
    a = make_raw([30, 10], live_time_s=100.0, real_time_s=100.5, start_time=T_A)
    b = make_raw([50, 30], live_time_s=300.0, real_time_s=300.5, start_time=T_B)
    c = compare_count_rates(a, b, block_width=1, min_block_counts=1)
    # p = 100/400 = 0.25. block 1: n=80, (30-20)^2/(80*.1875) = 6.6667
    #                     block 2: n=40, (10-10)^2/(...)     = 0
    assert c.ndf == 2 and c.chi2 == pytest.approx(100.0 / 15.0)
    assert c.p_value == pytest.approx(math.exp(-c.chi2 / 2.0))  # chi2 sf for 2 dof
    assert c.rate_a.value == pytest.approx(0.4) and c.rate_a.stat == pytest.approx(
        math.sqrt(40) / 100
    )
    assert c.rate_b.value == pytest.approx(80 / 300)
    assert c.reduced_chi2 == pytest.approx(c.chi2 / 2)


def test_sparse_blocks_are_excluded() -> None:
    a = make_raw([30, 1, 0, 0], live_time_s=100.0, real_time_s=100.5, start_time=T_A)
    b = make_raw([50, 2, 0, 0], live_time_s=300.0, real_time_s=300.5, start_time=T_B)
    c = compare_count_rates(a, b, block_width=1, min_block_counts=20)
    assert (c.ndf, c.blocks_skipped) == (1, 3)
    with pytest.raises(ProcessingError, match="too few counts"):
        compare_count_rates(a, b, block_width=1, min_block_counts=1000)


def test_equal_backgrounds_are_consistent_and_different_ones_are_not() -> None:
    a = generate(bg_spec(31, 1000.0)).spectrum
    same = generate(bg_spec(32, 3000.0)).spectrum
    assert compare_count_rates(a, same).p_value > 1e-3
    hotter = BG_MODEL.model_copy(update={"constant_cps_per_channel": 0.06})  # +20 % flat part
    other = generate(bg_spec(33, 3000.0, hotter)).spectrum
    assert compare_count_rates(a, other).p_value < 1e-6


def test_p_values_are_uniform_under_the_null_hypothesis() -> None:
    p = np.array(
        [
            compare_count_rates(
                generate(bg_spec(2 * i, 40.0, n=512)).spectrum,
                generate(bg_spec(2 * i + 1, 90.0, n=512)).spectrum,
                block_width=16,
            ).p_value
            for i in range(300)
        ]
    )
    # fraction below 0.05 is binomial(300, 0.05): mean 15, sd 3.8 -> allow 2..34
    assert 2 <= int((p < 0.05).sum()) <= 34
    assert p.mean() == pytest.approx(0.5, abs=5.0 * (1 / math.sqrt(12)) / math.sqrt(300))


def test_comparison_refuses_dependent_spectra() -> None:
    a = make_raw([30, 10], start_time=T_A)
    later_save = make_raw([60, 25], live_time_s=200.0, real_time_s=202.0, start_time=T_A)
    with pytest.raises(ProcessingError, match="same acquisition"):
        compare_count_rates(a, later_save, block_width=1)


# ---------------------------------------------------------------- display smoothing
def test_display_smoothing_is_flagged_and_conserves_interior_values() -> None:
    raw = make_raw([0, 0, 9, 0, 0, 0, 3, 0, 0])
    sm = smooth_for_display(raw, 3)
    assert sm.kind is SpectrumKind.PROCESSED and sm.metadata["display_only"] == "true"
    assert sm.provenance[-1].parameters["display_only"] is True
    assert any("DISPLAY ONLY" in w for w in sm.warnings)
    np.testing.assert_allclose(sm.counts, [0, 3, 3, 3, 0, 1, 1, 1, 0])
    assert sm.counts.sum() == pytest.approx(raw.counts.sum())
    np.testing.assert_allclose(sm.variance_array()[1:4], [1.0, 1.0, 1.0])  # 9/9
    np.testing.assert_array_equal(raw.counts, [0, 0, 9, 0, 0, 0, 3, 0, 0])
    for bad in (2, 4, 1, 11):
        with pytest.raises(ProcessingError):
            smooth_for_display(raw, bad)
    with pytest.raises(ProcessingError, match="not measured counts"):
        subtract_background(sm, background())  # smoothed data cannot enter an analysis


# ---------------------------------------------------------------- real measurements
