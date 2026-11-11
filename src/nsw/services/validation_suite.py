"""Consolidated synthetic validation: one run that exercises every analysis engine on
generated spectra with known truth and reports each metric with a pass criterion.

Categories (kept apart in the report)
-------------------------------------
checked        a metric with a stated expectation and tolerance; it passes or fails
characterised  a number that describes behaviour where no ideal value applies (for
               example the bias of weak free-shape fits); reported, never "passed"

Nothing here concerns measurements: sources, efficiencies and nuclides are fictitious.

Metrics (T trials, truth theta, estimate est_t, reported standard uncertainty u_t)
----------------------------------------------------------------------------------
relative bias      mean(est_t - theta) / theta, with standard error sd(est_t)/(theta sqrt(T))
                   criterion: |bias| <= 5 standard errors
pull               z_t = (est_t - theta) / u_t
coverage           fraction of trials with |z_t| <= 1; expectation 0.6827 for a correct
                   uncertainty; criterion: within 5 binomial standard errors,
                   sqrt(0.6827 (1 - 0.6827) / T)
rate               k successes in T trials; a required rate of exactly 1 or 0 passes only
                   with k = T or k = 0
repeatability      the same seed must reproduce every number bit for bit (SHA-256 of the
                   canonical JSON of all metric values); a different seed must give means
                   that agree within 5 combined standard errors

Five standard errors are used throughout because the suite makes many comparisons: a
correct implementation should essentially never fail by chance, while an error of a few
per cent in an uncertainty is still detected with a few hundred trials.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta

import numpy as np

import nsw
from nsw.analysis.activity import combine_lines, line_activity
from nsw.analysis.efficiency import (
    EfficiencyCurve,
    GammaLine,
    MeasurementTiming,
    ReferenceSource,
    efficiency_point,
    fit_efficiency_curve,
)
from nsw.analysis.energy_calibration import fit_energy_calibration
from nsw.analysis.fitting import FitResult, fit_peaks, fit_region_for
from nsw.analysis.identification import Confidence, IdentificationSettings
from nsw.analysis.resolution import ResolutionVariable, WidthPoint, fit_resolution
from nsw.core.calibration import CalibrationPoint
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum
from nsw.services import identification as identification_service
from nsw.services.validation import (
    STANDARD_FIT_CASES,
    FitStudyCase,
    run_detection_study,
    run_fit_study,
)
from nsw.simulation import presets, sources
from nsw.simulation.generator import SyntheticSpec, generate

IDEAL_COVERAGE = 0.6826894921370859
N_SIGMA = 5.0
REF = "arbitrary synthetic test value"
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Check:
    study: str
    metric: str
    category: str  # "checked" or "characterised"
    value: float
    expectation: str
    passed: bool | None  # None for characterised metrics
    detail: str = ""


@dataclass(frozen=True)
class ValidationRun:
    software: str
    numpy: str
    trials: int
    seed: int
    checks: tuple[Check, ...]

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.passed is False)

    @property
    def n_checked(self) -> int:
        return sum(1 for c in self.checks if c.passed is not None)

    def digest(self) -> str:
        """SHA-256 of all metric values: equal digests mean bit-identical results."""
        payload = [[c.study, c.metric, repr(c.value)] for c in self.checks]
        return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        doc = {
            "software": self.software,
            "numpy": self.numpy,
            "trials": self.trials,
            "seed": self.seed,
            "digest": self.digest(),
            "data": "synthetic only; fictitious sources and nuclides",
            "checks": [asdict(c) for c in self.checks],
        }
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"

    def to_markdown(self) -> str:
        lines = [
            "# NSW synthetic validation run",
            "",
            f"Software nsw {self.software}, NumPy {self.numpy}; {self.trials} trials per "
            f"study, seed {self.seed}.",
            "",
            "**Synthetic data only.** Sources, efficiencies and nuclides are fictitious; "
            "this run says how the software behaves when its model is exactly right and "
            "is not a validation of any detector or measurement.",
            "",
            f"Checked metrics: {self.n_checked}, failed: {len(self.failed)}. "
            f"Result digest: `{self.digest()}`.",
            "",
            "| Study | Metric | Value | Expectation | Result | Detail |",
            "|---|---|---|---|---|---|",
        ]
        for c in self.checks:
            verdict = "characterised" if c.passed is None else ("pass" if c.passed else "FAIL")
            lines.append(
                f"| {c.study} | {c.metric} | {c.value:.5g} | {c.expectation} | {verdict} | "
                f"{c.detail} |"
            )
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- helpers
def _coverage_check(study: str, metric: str, pulls: list[float]) -> Check:
    n = len(pulls)
    value = float(np.mean(np.abs(np.asarray(pulls)) <= 1.0)) if n else math.nan
    se = math.sqrt(IDEAL_COVERAGE * (1.0 - IDEAL_COVERAGE) / n) if n else math.nan
    ok = n > 0 and abs(value - IDEAL_COVERAGE) <= N_SIGMA * se
    return Check(
        study,
        metric,
        "checked",
        value,
        f"0.683 ± {N_SIGMA:g}·{se:.3f}",
        ok,
        f"{n} trials; pull mean {float(np.mean(pulls)):+.3f}, sd {float(np.std(pulls, ddof=1)):.3f}"
        if n > 1
        else f"{n} trials",
    )


def _bias_check(study: str, metric: str, estimates: list[float], truth: float) -> Check:
    e = np.asarray(estimates)
    n = e.size
    rel = float(e.mean() / truth - 1.0)
    se = float(e.std(ddof=1) / (abs(truth) * math.sqrt(n)))
    return Check(
        study,
        metric,
        "checked",
        rel,
        f"0 ± {N_SIGMA:g}·{se:.2g}",
        abs(rel) <= N_SIGMA * se,
        f"{n} trials",
    )


def _rate_check(study: str, metric: str, k: int, n: int, required: float) -> Check:
    value = k / n if n else math.nan
    return Check(
        study, metric, "checked", value, f"exactly {required:g}", value == required, f"{k} of {n}"
    )


def _spectrum_and_fits(
    source: sources.SyntheticSource, seed: int, live: float, start_offset_days: float = 0.0
) -> tuple[Spectrum, list[FitResult], SyntheticSpec, list[dict[str, float]]]:
    start = sources.REFERENCE_TIME + timedelta(days=start_offset_days)
    spec = SyntheticSpec(
        synthetic_id=f"suite-{source.name.lower()}-{seed}",
        live_time_s=live,
        real_time_s=live,
        seed=seed,
        lines=sources.line_rates(
            source, sources.SYNTHETIC_EFFICIENCY, start, live, continuum_ratio=1.0
        ),
        background=presets.HPGE_BACKGROUND,
        **presets.HPGE_BASE,
    )
    result = generate(spec)
    truth = list(result.truth["lines"])
    fits: list[FitResult] = []
    for line in truth:
        c, w = line["centroid_channel"], line["fwhm_channels"]
        first, last = fit_region_for(result.spectrum, [c], w)
        fits.append(fit_peaks(result.spectrum, first, last, [c], w))
    return result.spectrum, fits, spec, truth


# ---------------------------------------------------------------------- studies
CHECKED_FIT_CASES = ("HPGe-like, high count", "HPGe-like, medium count")
CHECKED_FIXED_CASE = "low count, position and width fixed"
CHARACTERISED_FIT_CASES = ("HPGe-like, low count", "background-dominated peak")


def study_peak_fitting(trials: int, seed: int) -> list[Check]:
    by_name: dict[str, FitStudyCase] = {c.name: c for c in STANDARD_FIT_CASES}
    checks: list[Check] = []
    for name in (*CHECKED_FIT_CASES, CHECKED_FIXED_CASE, *CHARACTERISED_FIT_CASES):
        r = run_fit_study(by_name[name], trials, seed)
        study = f"peak fit: {name}"
        a = r.area
        if name in CHARACTERISED_FIT_CASES:
            checks.append(
                Check(
                    study,
                    "area relative bias",
                    "characterised",
                    a.relative_bias,
                    "known limitation: weak free-shape fits are biased high",
                    None,
                    f"± {a.bias_se / a.truth:.2g}; failure rate {r.failure_rate:.3f}",
                )
            )
            continue
        se = a.bias_se / a.truth
        checks.append(
            Check(
                study,
                "area relative bias",
                "checked",
                a.relative_bias,
                f"0 ± {N_SIGMA:g}·{se:.2g}",
                abs(a.relative_bias) <= N_SIGMA * se,
                f"{r.n_ok} of {r.n_trials} fits usable",
            )
        )
        cov_se = math.sqrt(IDEAL_COVERAGE * (1 - IDEAL_COVERAGE) / max(r.n_ok, 1))
        checks.append(
            Check(
                study,
                "area coverage (1 sigma)",
                "checked",
                a.coverage_1,
                f"0.683 ± {N_SIGMA:g}·{cov_se:.3f}",
                abs(a.coverage_1 - IDEAL_COVERAGE) <= N_SIGMA * cov_se,
                f"pull sd {a.pull_sd:.3f}",
            )
        )
        checks.append(
            Check(
                study,
                "fit failure rate",
                "checked",
                r.failure_rate,
                "<= 0.01",
                r.failure_rate <= 0.01,
            )
        )
    return checks


def study_peak_search(trials: int, seed: int) -> list[Check]:
    strong = run_detection_study(200.0, 20.0, 6.0, (6.0, 12.0), n_trials=trials, seed=seed)
    weak = run_detection_study(100.0, 20.0, 6.0, (6.0, 12.0), n_trials=trials, seed=seed)
    return [
        Check(
            "peak search: 200 counts on 20 per channel",
            "detection probability",
            "checked",
            strong.detection_probability,
            ">= 0.99",
            strong.detection_probability >= 0.99,
            f"{strong.n_trials} trials",
        ),
        Check(
            "peak search: peak-free spectra",
            "false candidates per 1000 channels",
            "checked",
            strong.false_per_1000_channels,
            "<= 0.1 at threshold 4",
            strong.false_per_1000_channels <= 0.1,
            f"{strong.false_trials} spectra of 400 channels",
        ),
        Check(
            "peak search: 100 counts on 20 per channel",
            "detection probability",
            "characterised",
            weak.detection_probability,
            "near the detection threshold",
            None,
            f"± {weak.detection_se:.3f}",
        ),
    ]


def study_calibration_and_resolution(trials: int, seed: int) -> list[Check]:
    """Energy calibration and resolution model recovered from fitted SYN-CAL spectra."""
    base = presets.HPGE_BASE
    offset, gain = base["energy_offset_kev"], base["energy_gain_kev_per_channel"]
    model = base["resolution"]
    pulls: dict[str, list[float]] = {"offset": [], "gain": [], "a": [], "b": []}
    gains: list[float] = []
    p_values: list[float] = []
    unusable = 0
    for t in range(trials):
        _, fits, _, truth = _spectrum_and_fits(sources.SYN_CAL, seed + t, 5000.0)
        if not all(f.ok for f in fits):
            unusable += 1
            continue
        points = [
            CalibrationPoint(
                channel=f.peaks[0].centroid,
                channel_uncertainty=f.peaks[0].centroid_unc,
                reference_value=line["energy_kev"],
                reference_uncertainty=0.0,
                reference_source=REF,
            )
            for f, line in zip(fits, truth, strict=True)
        ]
        cal = fit_energy_calibration(points, 1)
        u = cal.coefficient_uncertainties
        pulls["offset"].append((float(cal.coefficients[0]) - offset) / float(u[0]))
        pulls["gain"].append((float(cal.coefficients[1]) - gain) / float(u[1]))
        gains.append(float(cal.coefficients[1]))
        p_values.append(cal.p_value)
        widths = [
            WidthPoint(line["energy_kev"], gain * f.peaks[0].fwhm, gain * f.peaks[0].fwhm_unc)
            for f, line in zip(fits, truth, strict=True)
        ]
        res = fit_resolution(widths, ResolutionVariable.ENERGY_KEV)
        if not all(res.fitted_terms[:2]):
            continue  # a term on its bound has no symmetric uncertainty
        ru = np.sqrt(np.diag(res.covariance))
        pulls["a"].append((float(res.coefficients[0]) - model.a) / float(ru[0]))
        pulls["b"].append((float(res.coefficients[1]) - model.b) / float(ru[1]))
    study = "energy calibration (8 lines, linear)"
    false_alarm = float(np.mean(np.asarray(p_values) < 0.05))
    fa_se = math.sqrt(0.05 * 0.95 / max(len(p_values), 1))
    return [
        _bias_check(study, "gain relative bias", gains, gain),
        _coverage_check(study, "gain coverage (1 sigma)", pulls["gain"]),
        _coverage_check(study, "offset coverage (1 sigma)", pulls["offset"]),
        Check(
            study,
            "fraction of fits with p < 0.05",
            "checked",
            false_alarm,
            f"0.05 ± {N_SIGMA:g}·{fa_se:.3f}",
            abs(false_alarm - 0.05) <= N_SIGMA * fa_se,
            f"{len(p_values)} calibrations; {unusable} spectra with an unusable peak fit",
        ),
        _coverage_check("resolution model FWHM² = a + bE", "a coverage (1 sigma)", pulls["a"]),
        _coverage_check("resolution model FWHM² = a + bE", "b coverage (1 sigma)", pulls["b"]),
    ]


def _curve_from(seed: int) -> EfficiencyCurve | None:
    cal = sources.SYN_CAL
    start_days = 40.0
    _, fits, spec, _ = _spectrum_and_fits(cal, seed, 20000.0, start_days)
    if not all(f.ok for f in fits):
        return None
    start = sources.REFERENCE_TIME + timedelta(days=start_days)
    timing = MeasurementTiming(start, spec.live_time_s, spec.real_time_s)
    certificate = ReferenceSource(
        cal.name, cal.activity_bq, 0.0, cal.reference_time, cal.half_life_s, 0.0, REF, True
    )
    points = [
        efficiency_point(
            f.peaks[0].area,
            f.peaks[0].area_unc,
            GammaLine(ln.energy_kev, ln.emission_probability, 0.0, REF),
            certificate,
            timing,
        )
        for f, ln in zip(fits, cal.lines, strict=True)
    ]
    return fit_efficiency_curve(points, order=2, geometry="synthetic")


def study_efficiency_and_activity(trials: int, seed: int) -> list[Check]:
    """Full chain per trial: efficiency curve from a SYN-CAL spectrum, then the activity of
    SYN-X at its reference time from a spectrum taken ten days later."""
    sample = sources.SYN_X
    n = max(trials // 4, 20)
    pulls: list[float] = []
    values: list[float] = []
    inconsistent = 0
    for t in range(n):
        curve = _curve_from(seed + 100_000 + t)
        if curve is None:
            continue
        _, fits, spec, _ = _spectrum_and_fits(sample, seed + 200_000 + t, 5000.0, 10.0)
        if not all(f.ok for f in fits):
            continue
        start = sources.REFERENCE_TIME + timedelta(days=10.0)
        timing = MeasurementTiming(start, spec.live_time_s, spec.real_time_s)
        results = [
            line_activity(
                f.peaks[0].area,
                f.peaks[0].area_unc,
                GammaLine(ln.energy_kev, ln.emission_probability, 0.0, REF),
                curve,
                timing,
                sample.half_life_s,
                sample.reference_time,
            )
            for f, ln in zip(fits, sample.lines, strict=True)
        ]
        combined = combine_lines(results, curve, 0.0, sample.reference_time)
        inconsistent += 0 if combined.consistent else 1
        values.append(combined.activity.value)
        pulls.append((combined.activity.value - sample.activity_bq) / combined.activity.uncertainty)
    study = "efficiency curve and activity (full chain)"
    return [
        _bias_check(study, "activity relative bias", values, sample.activity_bq),
        _coverage_check(study, "activity coverage (1 sigma)", pulls),
        Check(
            study,
            "fraction of inconsistent line sets",
            "checked",
            inconsistent / max(len(values), 1),
            "<= 0.02 (test level 0.001)",
            inconsistent / max(len(values), 1) <= 0.02,
            f"{len(values)} trials",
        ),
    ]


def study_identification(trials: int, seed: int) -> list[Check]:
    """Candidate ranking on SYN-X spectra against the fictitious library with decoys."""
    library = identification_service.fictitious_library()
    settings = IdentificationSettings(extra_energy_unc_kev=0.05)
    n = max(trials // 4, 20)
    first = wrong_accept = evaluated = 0
    for t in range(n):
        spectrum, fits, _, _ = _spectrum_and_fits(sources.SYN_X, seed + 300_000 + t, 5000.0)
        if not all(f.ok for f in fits):
            continue
        try:
            result = identification_service.identify(spectrum, fits, library, settings)
        except ProcessingError:
            continue
        evaluated += 1
        first += 1 if result.candidates and result.candidates[0].nuclide == "SYN-X" else 0
        accepted = (Confidence.PROBABLE, Confidence.CONSISTENT)
        wrong_accept += sum(
            1 for c in result.candidates if c.nuclide != "SYN-X" and c.confidence in accepted
        )
    study = "nuclide candidates (SYN-X with coincident decoys)"
    return [
        _rate_check(study, "true nuclide ranked first", first, evaluated, 1.0),
        _rate_check(study, "decoys classed probable or better", wrong_accept, evaluated, 0.0),
    ]


STUDIES: tuple[tuple[str, Callable[[int, int], list[Check]]], ...] = (
    ("peak search", study_peak_search),
    ("peak fitting", study_peak_fitting),
    ("energy calibration and resolution", study_calibration_and_resolution),
    ("efficiency and activity", study_efficiency_and_activity),
    ("nuclide candidates", study_identification),
)


def run_suite(
    trials: int = 200, seed: int = 50_000, progress: ProgressCallback | None = None
) -> ValidationRun:
    """Run every study. Deterministic for given ``trials`` and ``seed``."""
    if trials < 40:
        raise ProcessingError("at least 40 trials are needed for meaningful statistics")
    checks: list[Check] = []
    for name, study in STUDIES:
        if progress is not None:
            progress(name)
        checks.extend(study(trials, seed))
    return ValidationRun(
        software=nsw.__version__,
        numpy=np.__version__,
        trials=trials,
        seed=seed,
        checks=tuple(checks),
    )


def repeatability(first: ValidationRun, second: ValidationRun) -> list[Check]:
    """Compare two runs. Same seed and trials: the digests must be equal. Different seed:
    every checked metric must still pass (statistical repeatability)."""
    if (first.trials, first.seed) == (second.trials, second.seed):
        same = first.digest() == second.digest()
        return [
            Check(
                "repeatability",
                "identical results for identical seed",
                "checked",
                1.0 if same else 0.0,
                "bit-identical (equal SHA-256 digest)",
                same,
                first.digest()[:16],
            )
        ]
    failures = len(first.failed) + len(second.failed)
    largest = max(
        (
            abs(a.value - b.value)
            for a, b in zip(first.checks, second.checks, strict=True)
            if a.passed is not None
        ),
        default=0.0,
    )
    return [
        Check(
            "repeatability",
            "checked metrics failing in either of two independent runs",
            "checked",
            float(failures),
            "exactly 0",
            failures == 0,
            f"seeds {first.seed} and {second.seed}; largest change of a metric {largest:.3g}",
        )
    ]
