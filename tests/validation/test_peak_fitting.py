"""Statistical validation of peak search and fitting against synthetic ground truth.

This is software verification on a simulated detector, never experimental validation.
The metrics (bias, pull, coverage, failure rate, detection probability) are defined in
``nsw.services.validation``.

Tolerances
----------
* Monte Carlo over n trials: a quantity with ideal value v and standard error s must lie
  within 5 s of v (a correct implementation fails with probability < 1e-6 per check):
  pull mean 0 +- 5/sqrt(n); pull sd 1 +- 5/sqrt(2n); a fraction f +- 5 sqrt(f(1-f)/n).
* Fixed-seed scenario fits: every pull |est - truth| / u < 4. With about 100 pulls the
  chance that a correct implementation exceeds this on a new seed is under 1 %; the
  seeds are fixed, so the test is deterministic.
* Where the estimators are known to be imperfect (low counts) the bounds are the
  documented performance limits, stated at each test.
"""

from __future__ import annotations

import math

import pytest

from nsw.analysis.fitting import (
    FWHM_PER_SIGMA,
    FitMethod,
    FitResult,
    FitSettings,
    fit_candidates,
    fit_peaks,
    fit_region_for,
    multiplet_evidence,
)
from nsw.analysis.peaks import PeakSearchSettings, find_peak_candidates
from nsw.detectors.profiles import DetectorType
from nsw.services.validation import (
    FitStudyCase,
    ParameterStats,
    run_detection_study,
    run_fit_study,
)
from nsw.simulation import presets
from nsw.simulation.generator import (
    ResolutionModel,
    SyntheticLine,
    SyntheticResult,
    SyntheticSpec,
    generate,
)

N = 300
COVERAGE_1 = 0.6827


def scenario(name: str) -> SyntheticResult:
    specs = {s.synthetic_id: s for s in (*presets.hpge_scenarios(), *presets.nai_scenarios())}
    return generate(specs[name])


def fit_true_lines(res: SyntheticResult, half_width: float = 3.0) -> list[tuple[dict, FitResult]]:  # type: ignore[type-arg]
    out = []
    for line in res.truth["lines"]:
        c, w = line["centroid_channel"], line["fwhm_channels"]
        first, last = fit_region_for(res.spectrum, [c], w, half_width)
        out.append((line, fit_peaks(res.spectrum, first, last, [c + 0.4], 1.2 * w)))
    return out


def pulls(line: dict, r: FitResult) -> dict[str, float]:  # type: ignore[type-arg]
    p = r.peaks[0]
    return {
        "area": (p.area - line["expected_peak_counts"]) / p.area_unc,
        "centroid": (p.centroid - line["centroid_channel"]) / p.centroid_unc,
        "fwhm": (p.fwhm - line["fwhm_channels"]) / p.fwhm_unc,
    }


def assert_ideal(stats: ParameterStats, n: int) -> None:
    assert abs(stats.bias) <= 5 * stats.bias_se
    assert abs(stats.pull_mean) <= 5 / math.sqrt(n)
    assert abs(stats.pull_sd - 1) <= 5 / math.sqrt(2 * n)
    assert abs(stats.coverage_1 - COVERAGE_1) <= 5 * math.sqrt(COVERAGE_1 * (1 - COVERAGE_1) / n)


# ---------------------------------------------------------------- fixed scenarios
@pytest.mark.parametrize(
    "name",
    [
        "hpge-isolated-peaks",
        "hpge-isolated-peaks-no-continuum",
        "hpge-high-count",
        "nai-isolated-peaks-no-continuum",
    ],
)
def test_isolated_lines_are_recovered_within_their_uncertainties(name: str) -> None:
    res = scenario(name)
    for line, r in fit_true_lines(res):
        assert r.ok and not r.poor_fit, (line["label"], r.flags, r.p_value)
        for key, value in pulls(line, r).items():
            assert abs(value) < 4, (line["label"], key, value)
        assert r.peaks[0].energy_kev == pytest.approx(
            line["energy_kev"], abs=1.0
        )  # synthetic truth


def test_high_count_fit_reaches_the_precision_that_needs_the_binned_model() -> None:
    # 2e6 counts, FWHM 4 channels: the width is determined to 0.07 %. A point-sampled
    # Gaussian would be biased by 1/(24 sigma^2) = 1.4 % in sigma, i.e. about 20 sigma.
    res = scenario("hpge-high-count")
    line, r = fit_true_lines(res)[0]
    p = r.peaks[0]
    assert p.fwhm_unc / p.fwhm < 1e-3
    assert abs(p.fwhm - line["fwhm_channels"]) < 4 * p.fwhm_unc
    assert abs(p.area - line["expected_peak_counts"]) < 4 * p.area_unc
    # never below the Poisson limit sqrt(N); a little above it because of the continuum
    assert math.sqrt(p.area) <= p.area_unc < 1.15 * math.sqrt(p.area)


def test_no_silent_failure_when_the_continuum_is_not_linear() -> None:
    # NaI-like scenarios with the schematic Compton continuum: smeared edges fall inside
    # some fit regions and a straight line cannot describe them. Every line must either be
    # recovered or be reported as a poor fit; a wrong result must never look acceptable.
    for name in ("nai-isolated-peaks", "nai-high-count"):
        outcomes = []
        for line, r in fit_true_lines(scenario(name)):
            worst = max(abs(v) for v in pulls(line, r).values()) if r.ok else math.inf
            assert r.poor_fit or not r.ok or worst < 4, (name, line["label"], worst, r.p_value)
            outcomes.append(r.poor_fit)
        assert name != "nai-high-count" or any(outcomes)  # the inadequacy is detected


def test_low_count_scenario_lines() -> None:
    # about 30 expected counts per line: small-sample regime, 1-sigma intervals are known
    # to under-cover slightly (see the Monte Carlo test below), so allow 4.5
    for name in ("hpge-low-count", "nai-low-count"):
        for line, r in fit_true_lines(scenario(name)):
            if not r.ok:
                continue
            assert abs(pulls(line, r)["area"]) < 4.5, (name, line["label"])
            assert "deviance" in r.gof_method or r.p_value > 1e-3


# ---------------------------------------------------------------- search
@pytest.mark.parametrize(
    ("name", "scales"),
    [
        ("hpge-isolated-peaks", (4.0, 8.0)),
        ("hpge-isolated-peaks-no-continuum", (4.0, 8.0)),
        ("hpge-high-count", (4.0, 8.0)),
        ("nai-isolated-peaks", (8.0, 16.0, 28.0)),
        ("nai-isolated-peaks-no-continuum", (8.0, 16.0, 28.0)),
    ],
)
def test_search_finds_every_true_line(name: str, scales: tuple[float, ...]) -> None:
    res = scenario(name)
    found = find_peak_candidates(res.spectrum, PeakSearchSettings(fwhm_channels=scales))
    for line in res.truth["lines"]:
        near = [
            c
            for c in found.candidates
            if abs(c.channel - line["centroid_channel"]) < 0.5 * line["fwhm_channels"]
        ]
        assert len(near) == 1, line["label"]
        c = near[0]
        assert abs(c.channel - line["centroid_channel"]) < max(5 * c.channel_uncertainty, 0.15)
        assert c.fwhm_estimate == pytest.approx(line["fwhm_channels"], rel=0.15)
        assert c.area_estimate == pytest.approx(line["expected_peak_counts"], rel=0.15)
        assert c.energy_kev == pytest.approx(line["energy_kev"], abs=3.0)  # synthetic truth
    if "no-continuum" in name:
        assert len(found.candidates) == len(res.truth["lines"])  # nothing else in the spectrum


@pytest.mark.parametrize("name", ["hpge-background-only", "nai-background-only"])
def test_search_reports_nothing_in_background_only_spectra(name: str) -> None:
    res = scenario(name)
    scales = (4.0, 8.0) if name.startswith("hpge") else (8.0, 16.0, 28.0)
    found = find_peak_candidates(res.spectrum, PeakSearchSettings(fwhm_channels=scales))
    assert found.candidates == ()


def test_search_and_fit_pipeline_on_resolved_doublet() -> None:
    res = scenario("hpge-overlapping-doublet")
    found = find_peak_candidates(res.spectrum, PeakSearchSettings(fwhm_channels=(4.0, 8.0)))
    truth = res.truth["lines"]
    doublet = [c for c in found.candidates if 2380 < c.channel < 2430]
    assert len(doublet) == 2 and doublet[0].group == doublet[1].group
    fits = [f for f in fit_candidates(res.spectrum, found) if f.group == doublet[0].group]
    (fit,) = fits
    r = fit.result
    assert r is not None and r.ok and not r.poor_fit and r.n_peaks == 2
    for line, p in zip(truth, sorted(r.peaks, key=lambda q: q.centroid), strict=True):
        assert abs(p.area - line["expected_peak_counts"]) < 4 * p.area_unc
        assert abs(p.centroid - line["centroid_channel"]) < 4 * p.centroid_unc
    ev = multiplet_evidence(res.spectrum, r.first_channel, r.last_channel, [2398.0, 2410.0], 6.0)
    assert ev.supported and ev.separations_fwhm[0] == pytest.approx(2.0, abs=0.05)


def unresolved_spec(second_rate: float, seed: int = 4242) -> SyntheticSpec:
    """NaI-like pair 40 keV apart at FWHM 44 keV (0.9 FWHM), no continuum or background."""
    lines = [SyntheticLine(energy_kev=600.0, rate_cps=30.0, label="a")]
    if second_rate > 0:
        lines.append(SyntheticLine(energy_kev=640.0, rate_cps=second_rate, label="b"))
    return SyntheticSpec(
        synthetic_id=f"nai-pair-{second_rate:g}",
        detector_type=DetectorType.NAI_TL,
        detector_id="synthetic-nai",
        n_channels=512,
        energy_offset_kev=0.0,
        energy_gain_kev_per_channel=3.0,
        resolution=ResolutionModel(a=0.0, b=3.24),
        lines=tuple(lines),
        live_time_s=1000.0,
        real_time_s=1000.0,
        seed=seed,
    )


def test_unresolved_pair_search_gives_one_candidate_and_fit_reports_strong_correlation() -> None:
    res = generate(unresolved_spec(10.0))
    found = find_peak_candidates(res.spectrum, PeakSearchSettings(fwhm_channels=(16.0, 28.0)))
    assert len(found.candidates) == 1  # the search cannot separate lines 0.9 FWHM apart
    ev = multiplet_evidence(res.spectrum, 150, 265, [200.0, 213.3], 14.7)
    # 30000 + 10000 counts: the broadening is statistically significant ...
    assert ev.supported and ev.delta_bic > 100
    # ... but the components are far from independent (the resolved HPGe doublet has an
    # area correlation of -0.02), and the statement says so
    assert ev.separations_fwhm[0] < 1.0
    assert ev.area_correlations[0] < -0.5
    assert "not proof" in ev.statement


def test_single_line_is_not_split_into_two_components() -> None:
    res = generate(unresolved_spec(0.0))
    ev = multiplet_evidence(res.spectrum, 150, 265, [196.0, 206.0], 14.7)
    assert not ev.supported


# ---------------------------------------------------------------- Monte Carlo
@pytest.mark.parametrize(
    "case",
    [
        FitStudyCase("high", 20000.0, 50.0, 6.0),
        FitStudyCase("medium", 500.0, 20.0, 6.0),
        FitStudyCase("nai-like", 20000.0, 200.0, 15.0),
        FitStudyCase("poisson at 8 counts/channel", 200.0, 8.0, 6.0),
        FitStudyCase("joint medium", 500.0, 20.0, 6.0, method="joint"),
    ],
    ids=lambda c: c.name,
)
def test_estimator_is_unbiased_with_correct_coverage(case: FitStudyCase) -> None:
    r = run_fit_study(case, N)
    assert r.failure_rate <= 0.01
    for stats in (r.area, r.centroid, r.sigma):
        assert_ideal(stats, r.n_ok)
    assert abs(r.false_alarm_rate - 0.05) <= 5 * math.sqrt(0.05 * 0.95 / r.n_ok)
    # reported uncertainty agrees with the observed scatter
    assert r.area.mean_uncertainty == pytest.approx(r.area.sd, rel=5 / math.sqrt(2 * r.n_ok))


def test_low_count_poisson_fit_documented_performance() -> None:
    # 30 expected counts on 0.6 counts/channel. Maximum likelihood is only asymptotically
    # unbiased and its intervals under-cover slightly; the documented limits are:
    # |relative area bias| < 5 %, 1-sigma coverage >= 0.55, failure rate <= 3 %.
    r = run_fit_study(FitStudyCase("low", 30.0, 0.6, 6.0), N)
    assert r.failure_rate <= 0.03
    assert abs(r.area.relative_bias) < 0.05
    for stats in (r.area, r.centroid, r.sigma):
        assert 0.55 <= stats.coverage_1 <= 0.80
    # goodness of fit from the exact deviance moments stays calibrated at low counts
    assert r.false_alarm_rate <= 0.05 + 5 * math.sqrt(0.05 * 0.95 / r.n_ok)


def test_least_squares_on_low_counts_is_worse_than_poisson_and_says_so() -> None:
    # 8 counts/channel: 1/n weights. Measured with 1000 trials: area bias +2.1 % +- 0.3 %
    # and 13.5 % of correct-model fits with p < 0.05 (Poisson: +0.3 % +- 0.3 %, 5.4 %).
    wls = run_fit_study(FitStudyCase("wls", 200.0, 8.0, 6.0, method="wls"), N)
    ml = run_fit_study(FitStudyCase("ml", 200.0, 8.0, 6.0), N)
    assert wls.false_alarm_rate > ml.false_alarm_rate
    assert wls.failure_rate >= ml.failure_rate  # zero-count channels make WLS impossible
    spectrum = scenario("hpge-isolated-peaks").spectrum
    r = fit_peaks(spectrum, 386, 410, [398.0], 4.0, FitSettings(method=FitMethod.WLS))
    assert any("Poisson likelihood is preferred" in w for w in r.warnings)


def test_joint_fit_with_sparse_background_warns() -> None:
    r = run_fit_study(FitStudyCase("joint low", 30.0, 0.6, 6.0, method="joint"), 60)
    assert r.n_ok > 40  # it runs; its intervals are documented as too narrow
    # the warning is attached to every such fit
    from nsw.analysis.fitting import fit_peaks_joint
    from nsw.services.validation import _background_spec, _spec

    gross = generate(_spec(1, 30.0, 0.6, 6.0)).spectrum
    background = generate(_background_spec(2, 0.6, 1.0)).spectrum
    fit = fit_peaks_joint(gross, background, 182, 218, [200.0], 6.0)
    assert any("understates the uncertainties" in w for w in fit.warnings)
    assert math.isnan(fit.p_value) and fit.gof_method == "none"


def test_detection_probability_and_false_detections() -> None:
    weak = run_detection_study(20.0, 0.6, 6.0, (6.0, 12.0), n_trials=200)
    strong = run_detection_study(50.0, 0.6, 6.0, (6.0, 12.0), n_trials=200)
    assert strong.detection_probability >= 0.97
    assert 0.1 < weak.detection_probability < 0.5  # measured 0.28 +- 0.014 with 1000 trials
    for study in (weak, strong, run_detection_study(200.0, 20.0, 6.0, (6.0, 12.0), n_trials=200)):
        # Gaussian expectation at threshold 4 is of order 0.02 per 1000 channels
        assert study.false_per_1000_channels <= 0.2


def test_fwhm_constant() -> None:
    assert FWHM_PER_SIGMA == pytest.approx(2.35482, abs=5e-6)


# ---------------------------------------------------------------- experimental data
