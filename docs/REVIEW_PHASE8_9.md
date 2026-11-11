# Independent Review of Phases 8 and 9

Date: 2026-11-11. Reviewer: a separate Claude Fable 5.2 session that had no part in
writing the code and was not allowed to change it. Scope: decay, efficiency, activity,
the nuclear-data model, nuclide candidate ranking, their services and tests.

A reviewer's approval is not proof. Every finding listed as fixed was first reproduced
by a test that failed on the old code (`tests/validation/test_review_findings.py`, 15
tests) and passes now. Findings listed as open were not reproduced here unless stated.

**Reviewer's headline:** the formulas (decay factors, generalised least squares for the
efficiency curve, combination of activities, Currie limit, intensity chi-square) were
checked numerically against independent calculations and found correct. The defects
were in the class and score logic of the candidate ranking and in the service gates.

## Fixed (reproduced, corrected, covered by a regression test)

| # | Finding | Correction |
|---|---|---|
| 1.1 | A good candidate was downgraded because a *rejected* candidate matched a superset of its peaks | only a probable or consistent candidate can subsume another |
| 1.2 | A matched line outside the efficiency curve entered the line fraction with weight P instead of P·ε and swamped the missing lines | such lines carry no weight; a warning says so |
| 1.3 | One line of known emission probability plus relative-intensity lines reached "probable" | a class above "possible" needs two peaks matched by lines of known emission probability |
| 1.4a | Two lines of one nuclide on the anchor peak: predictions used the whole area with one line's weight (factor 2 too high) | the anchor area is attributed to the summed weight of the lines on it |
| 1.6 | Two list entries for one unresolvable peak counted as two peaks, a way past the single-line rule | peaks closer than k combined energy uncertainties count as one |
| 1.7 | A missing emission-probability uncertainty was silently taken as 0 | warning added |
| 1.8 | Uniqueness mixed relative and per-decay intensities | per-decay lines only (by count if only relative lines match) |
| 1.9 | Relative-efficiency groups with unrelated normalisation could be fitted as one curve | refused |
| 2.1 | **Gate loophole:** a calibration verified on a synthetic spectrum could be attached to an experimental spectrum, which then passed the identification gate | `apply_calibration` refuses it; the identification service refuses every experimental spectrum while experimental verification is disabled |
| 2.2 | Fits of another spectrum were accepted | refused by comparing data hashes |
| 2.4 | The default energy range was the exact span of the calibration points, so the outermost calibration lines fell out by rounding | the whole spectrum is the default range |
| 2.5 | `LineActivity.quantity()` raised for zero or negative net areas | absolute statistical uncertainty carried; non-detections are reportable |
| 2.6 | A singular covariance raised a raw linear-algebra error | `ProcessingError` with an explanation (efficiency curve, activity combination, intensity test) |
| 2.7 | Evaluation names were compared with case and blanks ("ddep " next to "DDEP") | stored trimmed and upper-case |
| 2.8 | A non-finite half-life was accepted from a file | refused |
| 1.5 (part) | "Missing" is judged against the supplied fits, not the spectrum | stated in every result; the underlying limitation stays (below) |

## Open (documented, not corrected)

| # | Finding | Why open |
|---|---|---|
| 1.4b | If another nuclide shares the anchor peak, predicted areas are too high and a true nuclide can be marked down | needs a second pass with an unshared anchor; the shared peak is listed in the evidence |
| 1.5 | A line the analyst did not fit counts as missing; the detectable-area estimate uses the gross counts in the window, including an unfitted peak | needs a net-area estimate from the spectrum; planned with the desktop page |
| 1.10 | The sign of the half-life term is lost in the covariance between efficiency points measured before and after the reference time | affects multi-spectrum curves only; the service builds a curve from one spectrum |
| 2.3 | The efficiency and activity services take peak areas and timing as free numbers; the spectrum argument only selects the gate | binding to fit results belongs with the desktop pages and the project store |
| 2.9, 2.10 | Overflow for a measurement longer than about 1000 half-lives; NaN energies not rejected in curve evaluation | low severity |
| 2.11 | Pydantic validation can be bypassed with `model_copy(update=...)` | no code path does this |
| §4.1 | Phase 8 uses `GammaLine` (free-text reference); Phase 9 uses the provenance model; no converter | to be unified when real data are imported |
| §4.3 | The percentage guard only catches values above 2 (1.5 % typed as 1.5 is accepted) | cannot be decided by the software; stated in the import documentation |
| §4.4, §4.5 | Evaluation names and the fictitious/real distinction are declared by the file, not verifiable | inherent to imported data |
| §4.10 | An efficiency curve carries no origin (synthetic or measured) | to be added with persistence |

## Assumptions that need experimental evidence (reviewer's section 3)

Uniform dead-time losses during a measurement; independence of emission probabilities
between lines; the log-space linearisation at low counts; adequacy of a polynomial in
ln E below about 150 keV; no correction for coincidence summing, self-attenuation or
geometry differences; Gaussian errors in the k = 3 window without gain drift; the
nominal 5 %/5 % error rates of the Currie limit with this window; FWHM interpolated
between fitted peaks; emission-probability-only expectation without an efficiency
curve; the conventional thresholds (missing factor 2, intensity p 10⁻³, weights 0.5);
single-nuclide decay without ingrowth.

## Tests

The reviewer judged these independent: decay hand values and finite differences, the
whitened least-squares comparison, the closed-form two-line intensity test, the window
hand values, the Monte Carlo coverage test. Judged circular: the comparison of the
generator's and the analysis' decay law (both use one closed form), and the uniqueness
and line-fraction expectations that restate the formula. Not covered before this
review and now covered: subsumption by a rejected candidate, extrapolated matched
lines, mixed-convention records, a fitted calibration in the service, an experimental
spectrum with a verified record, fits from another spectrum, evaluation-name case,
singular covariances, non-detections.

Not reviewed: the fitting module, the generator, the interface, the documents. No
false-positive or false-negative rates of the candidate classes have been measured.
