# Decision Threshold, Detection Limit and Coverage Interval — Design (R7 B)

Status: **Approved by the owner on 2026-11-12**, including the decisions listed at the end.
Date: 2026-11-12.

This document describes NSW's intended implementation of the *characteristic limits*
of ionising-radiation measurements in the framework of the ISO 11929 series. It is
written from published literature and the public scope of the standard; it does not
reproduce the standard's text. **NSW will not claim conformity with ISO 11929** until
the implementation has been checked against the purchased text of the edition in force
and against an independent implementation (§7).

## 1. Which standard

- ISO 11929-1:2019 (elementary applications) was withdrawn on 12 December 2025 and
  replaced by **ISO 11929-1:2025** (https://www.iso.org/standard/69579.html; national
  catalogues list the 2025 edition). The series: Part 1 elementary applications, Part 2
  advanced applications (Monte Carlo propagation per JCGM 101 / GUM Supplement 1), Part
  3 unfolding and multichannel spectrometry (gamma and alpha spectra), Part 4 guidance
  and examples.
- For NSW, **Part 1** covers single-peak net areas with a background estimate; **Part
  3** covers fitted multiplets and spectrum unfolding; **Part 2** is the route when the
  model is non-linear or uncertainties are not small.
- The technical differences between the 2019 and 2025 editions could not be
  established from public sources (searched 2026-11-12). The owner (or the
  institution's library) needs to provide access to the 2025 texts before
  implementation is finalised.

## 2. Concepts (from the published Bayesian framework)

Weise et al., *Radiat. Prot. Dosim.* 121 (2006) 52, and the ISO 11929 series define,
for a non-negative measurand y with true value ỹ and a model giving its standard
uncertainty as a function of the true value, ũ(ỹ):

- **Decision threshold** y* = k₁₋α · ũ(0). A measured value y > y* is taken as evidence
  that the physical effect is present (probability of a false positive α).
- **Detection limit** y# = smallest true value for which the probability of a false
  negative is β: y# = y* + k₁₋β · ũ(y#), solved by fixed-point iteration from y# = 2y*.
  It exists only when k₁₋β · u_rel(w) < 1, where w is the conversion factor (e.g.
  1/(ε·P_γ·t) for activity) — if not, NSW reports "detection limit does not exist" with
  the reason.
- **Best estimate and coverage interval** for a measured y with standard uncertainty
  u(y), using the prior knowledge ỹ ≥ 0: with ω = Φ(y/u), the best estimate
  ŷ = y + u·exp(−y²/(2u²))/(ω√(2π)) and probabilistically symmetric limits
  y◁ = y − k_p·u, y▷ = y + k_q·u with p = ω(1 − γ/2), q = 1 − ωγ/2.
- Default k₁₋α = k₁₋β = 1.645 (α = β = 0.05), γ = 0.05; all configurable and reported.

Currie's limits (L. A. Currie, *Anal. Chem.* 40 (1968) 586) are the classical special
case; NSW's identification currently uses the approximate Currie form
L_D = 2.71 + 4.65√B (`analysis/identification.py`) for the "missing line" judgement.

## 2a. Selectable method (owner's request, 2026-11-12)

Both methods are available and the analyst chooses per analysis; the choice is part of
the result's settings and provenance and is printed in reports:

| Method | When to choose | Includes u(w) | Coverage interval |
|---|---|---|---|
| `iso11929` (default) | modern practice; activities whose conversion factor is uncertain | yes | yes (best estimate with ỹ ≥ 0) |
| `currie` | comparison with older reports, laboratory procedures that specify Currie, MDA conventions of other software | no (stated in the result) | no |

For counting measurements with exactly known w and α = β both give identical numbers
(tested: y# = 2y* + k²); they differ when w is uncertain. Default and probabilities
come from `analysis.detection_limits` in the settings file; the interface selector is
part of the UI work. Implemented: `nsw.analysis.limits` (counting model, both methods,
coverage interval) with closed-form and Monte Carlo tests
(`tests/validation/test_limits.py`); the fit-based ũ(ỹ) of §3.1 follows.

## 3. Measurement models in NSW

### 3.1 Net peak area from a fit (Part 1 / Part 3)

NSW's fits already provide the area N, the continuum under the peak and the full
covariance from the Fisher information. The function ũ(Ñ) is obtained **from the fit
model itself**: for a hypothetical true area Ñ at the fitted (or fixed) position and
width, with the fitted continuum, compute the expected Fisher information and invert
it — no counting-formula approximation is needed, and continuum uncertainty, region
width and multiplet overlap enter automatically. For a single peak on a flat continuum
this reduces to the familiar ũ²(Ñ) ≈ Ñ + B(1 + n_peak/n_cont) form, which is used as an
analytic cross-check.

Shape parameters for limits are **fixed** (position from the calibration or the
candidate, width from the resolution model): a free shape at Ñ = 0 is undefined.

### 3.2 Joint gross + background fits

The existing joint profile-likelihood fit (`fit_peaks_joint`) gives ũ(Ñ) in the same
way, including the background measurement's Poisson variance scaled by α².

### 3.3 Activity

A = N · w, w = 1 / (ε · P_γ · t_live · corrections). u_rel(w) from the efficiency curve
covariance, emission-probability uncertainty (from the reference record), live-time and
decay-correction uncertainties. ũ²(Ã) = w²·ũ²(Ñ) + Ã²·u_rel²(w). Only for synthetic
data until the experimental gates are lifted.

### 3.4 Low counts

When expected counts in the region are small (the existing threshold for the exact
deviance test), the Gaussian approximation behind k₁₋α·ũ(0) degrades. NSW will (a)
report the limits with a "low-count approximation" flag, and (b) offer the Part 2 route:
Monte Carlo propagation of the counting distributions to obtain y*, y# as quantiles
directly (seeded, recorded, reproducible).

## 4. Outputs

Per peak or nuclide line: measured value with u, decision (above / not above the
decision threshold), y*, y#, best estimate ŷ, coverage interval, parameters (α, β, γ,
k values), model used (§3.1/3.2/3.4), and every uncertainty component. Reports state:
"characteristic limits computed following the ISO 11929 framework; conformity not
verified" until §7 is complete.

## 5. Replacing the approximate Currie limit

Identification's missing-line test switches to y# from §3.1 with the fixed shape at the
expected position; a regression test documents the change in classifications on the
fictitious library; the identification algorithm version is bumped.

## 6. Implementation outline

`nsw.analysis.limits` (pure functions: `decision_threshold`, `detection_limit`,
`best_estimate_interval`, `u_tilde_from_fit`), used by `services.peaks` (net areas),
`services.efficiency` (activities) and `analysis.identification`. No UI change beyond
new table columns and a report section.

## 7. Validation

| Test | Method | Acceptance |
|---|---|---|
| Analytic cases | flat continuum, single peak: compare with closed-form Part 1 counting formulas | relative difference < 10⁻⁶ |
| Independent implementation | the same inputs in **UncertRadio** (open-source implementation of ISO 11929 parts 1–3, Thünen Institute) for a set of documented cases | agreement within UncertRadio's printed precision; differences explained |
| Worked examples | the numerical examples of ISO 11929-4 once the text is available | agreement |
| False-positive rate | Monte Carlo, true area 0, 10⁵ trials per case, several continuum levels | fraction above y* = α within 5 SE |
| Detection probability | true area = y# | fraction above y* = 1 − β within 5 SE |
| Coverage | true areas 0…10 y# | coverage of [y◁, y▷] = 1 − γ within 5 SE, with the low-count regime reported separately |
| Low-count route | expected counts < 10 | Monte Carlo quantile route meets the same rates |

All studies enter the validation runner with fixed seeds; results in
`VALIDATION_REPORT.md`. No laboratory data are used for validation.

## 8. Risks and limitations

- Without the standard's text, details (rounding, choice of quantiles, treatment of
  influence quantities) may differ → no conformity claim; §7 independent comparison.
- Systematic components (efficiency, emission probability) dominate y# for strong
  continua; their correlation between lines of one nuclide is handled only in the
  activity combination, not in per-line limits — documented.
- Coincidence summing and attenuation corrections are outside the current model.

## 9. Size

Medium-high: about one to two weeks including the Monte Carlo studies.

## 10. Decisions requested from the owner

1. Approve the approach (fit-based ũ, fixed shape, Part 2 Monte Carlo route at low
   counts).
2. Defaults α = β = γ = 0.05.
3. Can you obtain ISO 11929-1:2025 (and ideally parts 3 and 4) through your institution?
4. May UncertRadio be downloaded and run on this machine as the independent reference?

## 11. Implementation status (2026-11-12)

Implemented: counting-model limits (both methods), coverage interval, and §3.1 limits
from a Poisson peak fit (`poisson_u_tilde`, `services.roi.fit_limits`; continuum and
other peaks as nuisance parameters, position and width fixed), shown with every region
fit. Validated: closed forms, Currie's formula, equality of the methods for exact w,
and Monte Carlo false-positive / detection rates for counting (4 × 10⁵ trials) and for
fit-based limits (3 000 fits per case, both methods) within 5 standard errors. **Not
yet done:** comparison with UncertRadio and with the ISO 11929-4 examples (needs the
software download — approved — and the standard's text), the Part 2 Monte Carlo route
for very low counts, the joint gross+background fit (§3.2), activity limits with u(w) in
the interface, and replacing the approximate Currie limit inside identification.
Conformity with ISO 11929 is still not claimed.
