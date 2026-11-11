"""Validation of decay factors, efficiency points and curves, and activities.

All sources, nuclear data and efficiency curves here are arbitrary synthetic test
values (``nsw.simulation.sources``). Nothing is computed for the laboratory data.

References: hand calculations (in comments), finite differences, an independent
whitened least-squares solution, the synthetic ground truth, and Monte Carlo ensembles
(tolerance: five standard errors of the ideal values).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nsw.analysis import decay
from nsw.analysis.activity import combine_lines, line_activity, specific_activity
from nsw.analysis.efficiency import (
    EfficiencyPoint,
    GammaLine,
    MeasurementTiming,
    ReferenceSource,
    efficiency_point,
    fit_efficiency_curve,
    point_covariance,
    relative_points,
)
from nsw.analysis.fitting import fit_peaks, fit_region_for
from nsw.core import ProcessingError, Unit
from nsw.core.units import Quantity
from nsw.simulation import presets, sources
from nsw.simulation.generator import SyntheticSpec, generate

REF = "arbitrary synthetic test value"
T0 = sources.REFERENCE_TIME
LONG = 1e15  # half-life so long that decay is negligible


def gline(e: float, p: float, up: float = 0.0) -> GammaLine:
    return GammaLine(e, p, up, REF)


def ref_source(
    activity: float = 1000.0, unc: float = 0.0, half_life: float = LONG, **kw: float
) -> ReferenceSource:
    return ReferenceSource(
        "test", activity, unc, T0, half_life, kw.get("half_life_unc", 0.0), REF, True
    )


def timing(
    live: float = 100.0, real: float | None = None, start: datetime = T0
) -> MeasurementTiming:
    return MeasurementTiming(start, live, real if real is not None else live)


# ---------------------------------------------------------------- decay
def test_decay_factors_hand_values_and_limits() -> None:
    assert decay.decay_constant(100.0) == pytest.approx(math.log(2) / 100)
    assert decay.decay_factor(100.0, 100.0) == pytest.approx(0.5)
    assert decay.decay_factor(100.0, -100.0) == pytest.approx(2.0)  # back in time
    # f = (1 - e^{-ln2}) / ln2 = 0.5 / ln 2
    assert decay.in_measurement_factor(100.0, 100.0) == pytest.approx(0.5 / math.log(2))
    assert decay.in_measurement_factor(1e12, 1.0) == pytest.approx(1.0, abs=1e-12)
    assert decay.in_measurement_factor(1.0, 1e-9) == pytest.approx(1.0, abs=1e-9)
    for bad in (0.0, -1.0, math.inf):
        with pytest.raises(ProcessingError):
            decay.decay_constant(bad)
    with pytest.raises(ProcessingError):
        decay.in_measurement_factor(100.0, 0.0)
    assert decay.elapsed_seconds(T0, T0 + timedelta(hours=1)) == 3600.0
    with pytest.raises(ProcessingError, match="timezone-aware"):
        decay.elapsed_seconds(T0, T0.replace(tzinfo=UTC))


@pytest.mark.parametrize(("half_life", "t"), [(100.0, 30.0), (100.0, 400.0), (5e6, 1800.0)])
def test_half_life_derivatives_match_finite_differences(half_life: float, t: float) -> None:
    h = half_life * 1e-6
    num_d = (
        math.log(decay.decay_factor(half_life + h, t))
        - math.log(decay.decay_factor(half_life - h, t))
    ) / (2 * h)
    num_f = (
        math.log(decay.in_measurement_factor(half_life + h, t))
        - math.log(decay.in_measurement_factor(half_life - h, t))
    ) / (2 * h)
    assert decay.d_ln_decay_factor_d_half_life(half_life, t) == pytest.approx(num_d, rel=1e-6)
    assert decay.d_ln_in_measurement_factor_d_half_life(half_life, t) == pytest.approx(
        num_f, rel=1e-5
    )


def test_simulation_and_analysis_use_the_same_decay_law() -> None:
    src = sources.SYN_X
    start = T0 + timedelta(days=10)
    assert src.activity_at(start) == pytest.approx(
        src.activity_bq * decay.decay_factor(src.half_life_s, 10 * 86400.0), rel=1e-12
    )
    assert src.in_measurement_factor(7200.0) == pytest.approx(
        decay.in_measurement_factor(src.half_life_s, 7200.0), rel=1e-12
    )


# ---------------------------------------------------------------- efficiency points
def test_efficiency_point_hand_calculation() -> None:
    # 1000 counts from 1000 Bq x 0.5 photons/decay x 100 s = 50000 photons -> eps = 0.02
    p = efficiency_point(1000.0, 10.0, gline(500.0, 0.5, 0.005), ref_source(1000.0, 20.0), timing())
    assert p.efficiency == pytest.approx(0.02, rel=1e-9)
    assert p.rel_stat == pytest.approx(0.01) and p.rel_line == pytest.approx(0.01)
    assert p.rel_source == pytest.approx(0.02, rel=1e-6)
    assert p.rel_total == pytest.approx(math.sqrt(6e-4)) and p.absolute
    assert p.uncertainty == pytest.approx(0.02 * math.sqrt(6e-4))


def test_efficiency_point_decay_and_live_time() -> None:
    line = gline(500.0, 0.5)
    base = efficiency_point(1000.0, 10.0, line, ref_source(), timing()).efficiency
    # measured one half-life after the reference time: half the activity, twice the efficiency
    src = ref_source(half_life=86400.0 * 100)
    late = efficiency_point(1000.0, 10.0, line, src, timing(start=T0 + timedelta(days=100)))
    f = decay.in_measurement_factor(86400.0 * 100, 100.0)
    assert late.efficiency == pytest.approx(2 * base / f, rel=1e-9)
    # dead time: the same counts in half the live time mean twice the efficiency;
    # the real time enters only through the (here negligible) decay during the measurement
    busy = efficiency_point(1000.0, 10.0, line, ref_source(), timing(live=50.0, real=100.0))
    assert busy.efficiency == pytest.approx(2 * base, rel=1e-9)
    # half-life uncertainty contributes through d ln(D f)/dT
    src_u = ReferenceSource("s", 1000.0, 0.0, T0, 86400.0 * 100, 86400.0, REF, True)
    with_u = efficiency_point(1000.0, 10.0, line, src_u, timing(start=T0 + timedelta(days=100)))
    assert with_u.rel_source == pytest.approx(math.log(2) * 0.01, rel=1e-3)  # lambda dt * uT/T


def test_invalid_efficiency_inputs() -> None:
    line, src, t = gline(500.0, 0.5), ref_source(), timing()
    for area, unc in ((0.0, 1.0), (-5.0, 1.0), (100.0, 0.0)):
        with pytest.raises(ProcessingError):
            efficiency_point(area, unc, line, src, t)
    with pytest.raises(ProcessingError, match="source of its nuclear data"):
        GammaLine(500.0, 0.5, 0.0, " ")
    with pytest.raises(ProcessingError, match="emission probability"):
        GammaLine(500.0, 0.0, 0.0, REF)
    with pytest.raises(ProcessingError, match="provenance"):
        ReferenceSource("s", 1.0, 0.0, T0, 1.0, 0.0, "")
    with pytest.raises(ProcessingError, match="activity must be positive"):
        ReferenceSource("s", 0.0, 0.0, T0, 1.0, 0.0, REF)
    with pytest.raises(ProcessingError, match="real time"):
        MeasurementTiming(T0, 100.0, 50.0)


# ---------------------------------------------------------------- curve
MODEL = sources.SYNTHETIC_EFFICIENCY
ENERGIES = [ln.energy_kev for ln in sources.SYN_CAL.lines]


def exact_points(
    rel_stat: float = 0.01, rel_source: float = 0.0, source: str = "A"
) -> list[EfficiencyPoint]:
    return [
        EfficiencyPoint(e, MODEL.efficiency(e), rel_stat, 0.0, rel_source, source) for e in ENERGIES
    ]


def test_curve_recovers_exact_model_and_matches_independent_solution() -> None:
    curve = fit_efficiency_curve(exact_points(), order=2, geometry="synthetic point source")
    e0 = curve.reference_energy_kev
    # compare as functions (the reference energies differ): values at several energies
    for e in (90.0, 300.0, 1000.0, 1700.0):
        value, rel, extrapolated = curve.efficiency(e)
        assert value[0] == pytest.approx(MODEL.efficiency(e), rel=1e-10)
        assert rel[0] > 0 and not extrapolated[0]
    assert curve.chi2 == pytest.approx(0.0, abs=1e-10) and curve.ndf == 5 and curve.absolute
    assert e0 == pytest.approx(math.exp(np.mean(np.log(ENERGIES))))
    assert curve.efficiency(50.0)[2][0] and curve.efficiency(2500.0)[2][0]  # extrapolated
    assert curve.energy_range_kev == (80.0, 1800.0)
    assert all(
        any(w == u for w in curve.warnings) for u in ("true coincidence summing is not corrected",)
    )

    # independent solution: whiten with the Cholesky factor and use ordinary least squares
    rng = np.random.default_rng(4)
    pts = [
        EfficiencyPoint(
            p.energy_kev,
            p.efficiency * math.exp(rng.normal(0, 0.02)),
            0.02,
            0.01,
            0.03,
            "A" if i < 5 else "B",
        )
        for i, p in enumerate(exact_points())
    ]
    fit = fit_efficiency_curve(pts, order=2)
    v = point_covariance(pts)
    chol = np.linalg.cholesky(v)
    log = np.log(np.array(ENERGIES) / fit.reference_energy_kev)
    x = np.vstack([log**k for k in range(3)]).T
    y = np.log([p.efficiency for p in pts])
    xw, yw = np.linalg.solve(chol, x), np.linalg.solve(chol, y)
    ref, *_ = np.linalg.lstsq(xw, yw, rcond=None)
    np.testing.assert_allclose(fit.coefficients, ref, rtol=1e-9)
    np.testing.assert_allclose(fit.covariance, np.linalg.inv(xw.T @ xw), rtol=1e-8)
    assert fit.chi2 == pytest.approx(float(np.sum((yw - xw @ ref) ** 2)), rel=1e-9)


def test_common_source_uncertainty_is_correlated_not_averaged() -> None:
    plain = fit_efficiency_curve(exact_points(0.01, 0.0), order=2)
    shared = fit_efficiency_curve(exact_points(0.01, 0.05), order=2)
    _, rel_plain, _ = plain.efficiency(600.0)
    _, rel_shared, _ = shared.efficiency(600.0)
    # a 5 % activity uncertainty common to all points stays 5 % on the curve
    assert rel_shared[0] == pytest.approx(math.hypot(rel_plain[0], 0.05), rel=1e-6)
    # ... but cancels in the ratio of efficiencies at two energies
    ratio_variance = []
    for curve in (plain, shared):
        c = curve.relative_covariance([200.0, 1200.0])
        ratio_variance.append(c[0, 0] + c[1, 1] - 2 * c[0, 1])
    assert ratio_variance[1] == pytest.approx(ratio_variance[0], rel=1e-6)
    # two different sources: their uncertainties are independent
    mixed = [*exact_points(0.01, 0.05, "A")[:4], *exact_points(0.01, 0.05, "B")[4:]]
    v = point_covariance(mixed)
    assert v[0, 1] == pytest.approx(0.0025) and v[0, 5] == 0.0


def test_curve_rules() -> None:
    pts = exact_points()
    with pytest.raises(ProcessingError, match="needs at least 3 points"):
        fit_efficiency_curve(pts[:2], order=2)
    with pytest.raises(ProcessingError, match="order must be"):
        fit_efficiency_curve(pts, order=0)
    with pytest.raises(ProcessingError, match="same energy"):
        fit_efficiency_curve([pts[0], pts[0], pts[1]], order=1)
    exact = fit_efficiency_curve(pts[:3], order=2)
    assert exact.ndf == 0 and not exact.verifiable and math.isnan(exact.p_value)
    assert any("no redundancy" in w for w in exact.warnings)
    assert any("no geometry description" in w for w in exact.warnings)
    bad = list(pts)
    bad[3] = EfficiencyPoint(bad[3].energy_kev, bad[3].efficiency * 1.3, 0.01, 0.0, 0.0, "A")
    poor = fit_efficiency_curve(bad, order=2)
    assert poor.poor_fit and not poor.verifiable
    assert int(np.argmax(np.abs(poor.residuals))) == 3


def test_relative_efficiency_needs_no_activity_and_cannot_give_activity() -> None:
    lines = [
        gline(e, p.emission_probability)
        for e, p in zip(ENERGIES, sources.SYN_CAL.lines, strict=True)
    ]
    scale = 12345.0  # unknown activity x time: drops out
    areas = [
        (scale * MODEL.efficiency(ln.energy_kev) * ln.emission_probability, 1.0) for ln in lines
    ]
    pts = relative_points(areas, lines, "SYN-CAL run")
    assert pts[0].efficiency == 1.0 and not pts[0].absolute and pts[0].rel_source == 0.0
    for p in pts:
        assert p.efficiency == pytest.approx(
            MODEL.efficiency(p.energy_kev) / MODEL.efficiency(ENERGIES[0]), rel=1e-12
        )
    curve = fit_efficiency_curve(pts, order=2)
    assert not curve.absolute and any("arbitrary units" in w for w in curve.warnings)
    with pytest.raises(ProcessingError, match="cannot be mixed"):
        fit_efficiency_curve([*pts[:4], *exact_points()[4:]], order=2)
    with pytest.raises(ProcessingError, match="absolute efficiency curve"):
        line_activity(100.0, 10.0, lines[0], curve, timing(), LONG)
    with pytest.raises(ProcessingError, match="at least two lines"):
        relative_points(areas[:1], lines[:1], "x")


# ---------------------------------------------------------------- activity
def test_activity_hand_calculation_and_reference_time() -> None:
    curve = fit_efficiency_curve(exact_points(), order=2, geometry="g")
    line = gline(500.0, 0.2)
    eps = MODEL.efficiency(500.0)
    # N = eps P A t  ->  A = N / (eps P t)
    n = eps * 0.2 * 4000.0 * 1000.0
    r = line_activity(n, 0.01 * n, line, curve, timing(1000.0), LONG)
    assert r.activity_bq == pytest.approx(4000.0, rel=1e-9) and not r.extrapolated
    assert r.rel_stat == pytest.approx(0.01)
    # same counts, but the reference time is one half-life before the measurement
    half_life = 86400.0 * 30
    start = T0 + timedelta(days=30)
    f = decay.in_measurement_factor(half_life, 1000.0)
    late = line_activity(n, 0.01 * n, line, curve, timing(1000.0, start=start), half_life, T0)
    assert late.activity_bq == pytest.approx(2 * 4000.0 / f, rel=1e-9)
    at_start = line_activity(n, 0.01 * n, line, curve, timing(1000.0, start=start), half_life)
    assert at_start.activity_bq == pytest.approx(4000.0 / f, rel=1e-9)
    q = late.quantity(half_life_unc_s=0.0)
    assert q.unit is Unit.BECQUEREL and q.stat == pytest.approx(0.01 * late.activity_bq)
    assert q.sys == pytest.approx(late.activity_bq * late.rel_efficiency)
    # a negative net area is a valid (non-detection) estimate, not an error
    assert line_activity(-5.0, 10.0, line, curve, timing(1000.0), LONG).activity_bq < 0
    with pytest.raises(ProcessingError):
        line_activity(10.0, 0.0, line, curve, timing(1000.0), LONG)


def test_combination_of_lines_and_consistency_test() -> None:
    curve = fit_efficiency_curve(exact_points(0.005), order=2, geometry="g")
    lines = [gline(200.0, 0.5, 0.005), gline(650.0, 0.3, 0.003), gline(1250.0, 0.2, 0.002)]
    t = timing(2000.0)

    def area(line: GammaLine, activity: float = 5000.0) -> float:
        return MODEL.efficiency(line.energy_kev) * line.emission_probability * activity * 2000.0

    results = [line_activity(area(ln), math.sqrt(area(ln)), ln, curve, t, LONG) for ln in lines]
    combined = combine_lines(results, curve, 0.0, T0)
    assert combined.activity.value == pytest.approx(5000.0, rel=1e-9)
    assert combined.consistent and combined.ndf == 2 and combined.chi2 == pytest.approx(0, abs=1e-9)
    assert sum(combined.weights) == pytest.approx(1.0)
    best_single = min(r.quantity(0.0).uncertainty for r in results)
    assert combined.activity.uncertainty < best_single  # combining helps ...
    # ... but not below the uncertainty that all lines share through the efficiency curve
    floor = 5000.0 * math.sqrt(
        float(np.min(np.linalg.eigvalsh(curve.relative_covariance([200.0, 650.0, 1250.0]))))
    )
    assert combined.activity.sys > floor
    assert any("same source-detector geometry" in w for w in combined.warnings)

    off = list(results)
    off[1] = line_activity(
        1.3 * area(lines[1]), math.sqrt(area(lines[1])), lines[1], curve, t, LONG
    )
    bad = combine_lines(off, curve, 0.0, T0)
    assert not bad.consistent and any("not consistent with one activity" in w for w in bad.warnings)

    outside = line_activity(100.0, 10.0, gline(2500.0, 0.1), curve, t, LONG)
    assert outside.extrapolated
    assert any("extrapolated" in w for w in combine_lines([outside], curve, 0.0, T0).warnings)
    single = combine_lines(results[:1], curve, 0.0, T0)
    assert single.ndf == 0 and math.isnan(single.p_value) and single.consistent
    with pytest.raises(ProcessingError, match="non-detections"):
        combine_lines([line_activity(-5.0, 10.0, lines[0], curve, t, LONG)], curve, 0.0, T0)
    with pytest.raises(ProcessingError, match="no line results"):
        combine_lines([], curve, 0.0, T0)


def test_specific_activity() -> None:
    a = Quantity(1000.0, Unit.BECQUEREL, stat=10.0, sys=30.0)
    value, stat, sys = specific_activity(a, 0.5, 0.005)
    assert value == 2000.0 and stat == 20.0
    assert sys == pytest.approx(math.hypot(60.0, 2000.0 * 0.01))
    with pytest.raises(ProcessingError):
        specific_activity(a, 0.0)


# ---------------------------------------------------------------- synthetic spectra
def spectrum_of(
    source: sources.SyntheticSource, start: datetime, live: float, seed: int
) -> SyntheticSpec:
    real = live * 1.02
    return SyntheticSpec(
        synthetic_id=f"{source.name.lower()}-{seed}",
        live_time_s=live,
        real_time_s=real,
        seed=seed,
        lines=sources.line_rates(source, MODEL, start, real, continuum_ratio=1.0),
        background=presets.HPGE_BACKGROUND,
        **presets.HPGE_BASE,
    )


def fitted_areas(spec: SyntheticSpec) -> list[tuple[float, float]]:
    res = generate(spec)
    out = []
    for line in res.truth["lines"]:
        c, w = line["centroid_channel"], line["fwhm_channels"]
        first, last = fit_region_for(res.spectrum, [c], w)
        r = fit_peaks(res.spectrum, first, last, [c], w)
        assert r.ok, line["label"]
        out.append((r.peaks[0].area, r.peaks[0].area_unc))
    return out


def test_full_chain_on_synthetic_spectra_recovers_efficiency_and_activity() -> None:
    cal, sample = sources.SYN_CAL, sources.SYN_X
    cal_start = T0 + timedelta(days=40)
    cal_spec = spectrum_of(cal, cal_start, 20000.0, 501)
    cal_timing = MeasurementTiming(cal_start, cal_spec.live_time_s, cal_spec.real_time_s)
    certificate = ReferenceSource(
        cal.name,
        cal.activity_bq,
        0.01 * cal.activity_bq,
        cal.reference_time,
        cal.half_life_s,
        0.0,
        REF,
        True,
    )
    points = [
        efficiency_point(
            a, u, gline(ln.energy_kev, ln.emission_probability), certificate, cal_timing
        )
        for (a, u), ln in zip(fitted_areas(cal_spec), cal.lines, strict=True)
    ]
    curve = fit_efficiency_curve(points, order=2, geometry="synthetic: same for both runs")
    assert curve.verifiable and curve.ndf == 5
    for e in (100.0, 250.0, 650.0, 1250.0, 1700.0):
        value, rel, _ = curve.efficiency(e)
        assert abs(value[0] / MODEL.efficiency(e) - 1) < 4 * rel[0], e
        assert rel[0] < 0.03

    start = T0 + timedelta(days=10)  # a third of a half-life after the reference time
    spec = spectrum_of(sample, start, 5000.0, 502)
    t = MeasurementTiming(start, spec.live_time_s, spec.real_time_s)
    lines = [gline(ln.energy_kev, ln.emission_probability) for ln in sample.lines]
    results = [
        line_activity(a, u, ln, curve, t, sample.half_life_s, sample.reference_time)
        for (a, u), ln in zip(fitted_areas(spec), lines, strict=True)
    ]
    combined = combine_lines(results, curve, 0.0, sample.reference_time)
    assert combined.consistent
    assert abs(combined.activity.value - sample.activity_bq) < 4 * combined.activity.uncertainty
    assert combined.activity.uncertainty / combined.activity.value < 0.03
    # ignoring the decay since the reference time would be wrong by 2^(10/30) - 1 = 26 %
    at_start = line_activity(*fitted_areas(spec)[0], lines[0], curve, t, sample.half_life_s)
    assert at_start.activity_bq == pytest.approx(results[0].activity_bq * 2 ** (-10 / 30), rel=1e-9)


def test_monte_carlo_coverage_of_activity_with_correlated_calibration() -> None:
    """Calibration counts, sample counts and the certificate value of the calibration
    activity (2 %) all fluctuate; the reported uncertainty must cover the truth."""
    rng = np.random.default_rng(909)
    cal, sample = sources.SYN_CAL, sources.SYN_X
    cal_lines = [gline(ln.energy_kev, ln.emission_probability) for ln in cal.lines]
    smp_lines = [gline(ln.energy_kev, ln.emission_probability) for ln in sample.lines]
    t_cal, t_smp = timing(2000.0), timing(3000.0)
    mu_cal = np.array(
        [
            MODEL.efficiency(g.energy_kev) * g.emission_probability * cal.activity_bq * 2000.0
            for g in cal_lines
        ]
    )
    mu_smp = np.array(
        [
            MODEL.efficiency(g.energy_kev) * g.emission_probability * sample.activity_bq * 3000.0
            for g in smp_lines
        ]
    )
    n = 500
    pulls, p_values = np.empty(n), np.empty(n)
    for i in range(n):
        stated = cal.activity_bq * (1 + rng.normal(0, 0.02))  # certificate differs from truth
        cert = ReferenceSource("cal", stated, 0.02 * stated, T0, LONG, 0.0, REF, True)
        counts = rng.poisson(mu_cal)
        pts = [
            efficiency_point(float(c), math.sqrt(c), g, cert, t_cal)
            for c, g in zip(counts, cal_lines, strict=True)
        ]
        curve = fit_efficiency_curve(pts, order=2, geometry="g")
        results = [
            line_activity(float(c), math.sqrt(c), g, curve, t_smp, LONG)
            for c, g in zip(rng.poisson(mu_smp), smp_lines, strict=True)
        ]
        r = combine_lines(results, curve, 0.0, T0)
        pulls[i] = (r.activity.value - sample.activity_bq) / r.activity.uncertainty
        p_values[i] = r.p_value
    assert abs(pulls.mean()) < 5 / math.sqrt(n)
    assert abs(pulls.std(ddof=1) - 1) < 5 / math.sqrt(2 * n)
    cover = float(np.mean(np.abs(pulls) < 1))
    assert abs(cover - 0.6827) < 5 * math.sqrt(0.6827 * 0.3173 / n)
    assert abs(float(np.mean(p_values < 0.05)) - 0.05) < 5 * math.sqrt(0.05 * 0.95 / n)
