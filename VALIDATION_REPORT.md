# Validation Report

Last updated: 2026-11-11 (public snapshot). Software: nsw 0.1.0a1, Python 3.12.13,
NumPy 2.5.4, SciPy 1.18.1.

Three categories are kept strictly apart:

1. **Software verification** — code does what its specification says, checked against
   hand-derived or analytical values.
2. **Synthetic validation** — known ground truth is recovered from generated spectra.
3. **Experimental observations** — behaviour on real measurements. These confirm data
   handling; they are **not** a validation of detector physics, and no physical
   reference value has been reproduced yet.

Test run of record: 430 tests, all passing (231 unit, 41 integration, 158 validation);
97 % statement coverage of the non-GUI code. Tests that read private laboratory
measurements are not part of this repository.

## 1. Software verification

| Area | Evidence (test file) | Result |
|---|---|---|
| Units, uncertainty arithmetic, formatting | `unit/test_units.py` (hand-calculated cases) | pass |
| Spectrum validation, immutability, kinds, experimental/synthetic separation, calibration status | `unit/test_spectrum.py`, `unit/test_calibration.py` | pass |
| Detector profiles, settings | `unit/test_detectors_and_config.py` | pass |
| Layering rule (no Qt/SQLite/config in scientific packages) | `unit/test_architecture.py` | pass |
| Parsers for Lynx CSV, Lynx AVA, Canberra CNF on fabricated files; 11 + 8 + 4 malformed variants rejected with explicit diagnostics; unsupported content never accepted | `unit/test_io_adapters.py` | pass |
| Identity grouping, successive saves, name conflicts, integrity manifest | `unit/test_identity_and_integrity.py` | pass |
| Background arithmetic vs. hand calculation; all rejection rules | `validation/test_background.py` | pass |
| GUI workflows (offscreen): load on worker thread, display by channel, cursor, zoom/pan, comparison, metadata, export guard, background page | `integration/test_gui_*.py` | pass |
| Search kernel: zero area, symmetry, no response to a + bx; response to a binned Gaussian equals N s³/(s² + p² + 1/12)^{3/2} to 0.2 % | `unit/test_peak_engine.py` | pass |
| Fit model equals an independently coded binned Gaussian + line (1e-12); analytic Jacobian equals central finite differences (1e-6) for 5 model layouts incl. step and separate widths | `unit/test_peak_engine.py` | pass |
| Least squares on exact (noise-free) data returns the generating parameters (1e-6) and a covariance equal to an independently computed (JᵀWJ)⁻¹ (1e-5); amplitude uncertainty includes cov(N, σ) | `unit/test_peak_engine.py` | pass |
| Poisson fit with 12 counts in 3 channels and 57 empty channels: area = total counts, u(area) = √12, data untouched (no pseudo-counts) | `unit/test_peak_engine.py` | pass |
| Deviance moments vs. brute-force Poisson sums (1e-9); runs statistic vs. hand value | `unit/test_peak_engine.py` | pass |
| Joint fit: profiled β is a stationary point of the likelihood (1e-9), including empty channels and negative source term; returned score equals the numerical derivative | `unit/test_peak_engine.py` | pass |
| Refusals: Poisson on net data, least squares with zero variance, display-only spectra, undesignated or dependent background, invalid regions and settings | `unit/test_peak_engine.py` | pass |
| Peak tables, status labels, CSV export (energy column only when verified; no write into the dataset) | `unit/test_peak_service.py` | pass |
| GUI: peak search and fit run on worker threads; markers, fit overlay, residuals, provisional/synthetic banners, joint fit gated on a designated background | `integration/test_gui_peaks.py` | pass |
| Calibration records: redundancy required for VERIFIED, valid range, extrapolation flag, gain, energy uncertainty from covariance (hand value) | `unit/test_calibration.py` | pass |
| Calibration service: usable peaks only, mandatory source of every reference value, experimental calibrations never verified, CSV export without NumPy artefacts, stability across independent spectra only | `unit/test_calibration_service.py` | pass |
| GUI: Energy Calibration page (assignment table, fit, residual plot, order test, history, stability, export, refusals shown in the page) | `integration/test_gui_calibration.py` | pass |
| GUI: Resolution Analysis page (channel units and provisional for experimental data, keV and verified for synthetic data, a broadened peak detected by its residual and removed by the analyst, export); fixed-shape options of the fit page; fixed widths not offered as resolution data | `integration/test_gui_resolution.py` | pass |

## 2. Synthetic validation

All results below concern the generator's own mathematical model.

| Check | Reference | Tolerance and reason | Result |
|---|---|---|---|
| Peak bin integrals sum to the specified area | exact | 1e-12 relative (floating point) | pass |
| Peak centroid of the expectation | (E₀ − c₀)/c₁ | 1e-9 channels | pass |
| Peak variance of the expectation | σ² + 1/12 (Sheppard correction for unit bins) | 1e-6 relative, σ ≈ 2.5 channels | pass |
| Peak-centre channel content | A·erf(0.5/(σ√2)) | 1e-12 relative | pass |
| Continuum normalisation and Compton-edge cut-off | closed form | 1e-12; < 1e-6 of counts beyond edge + 6σ | pass |
| Realised component counts vs. expectation, all 12 scenarios | Poisson | 5 σ | pass |
| Ensemble of 400 seeds: mean and variance/mean of a 40-count peak | 40; 1 | 5 standard errors | pass |
| Channel pulls in the high-count scenario | mean 0, width 1 | 5/√N; ±0.1 | pass |
| Determinism for a fixed seed; file round trip | identical hash | exact | pass |
| Net of two independent backgrounds: channel pulls (2048 channels) | mean 0, width 1 | 5/√N, 5/√(2N) | pass |
| Estimated net variance vs. exact Poisson variance | ratio 1 | 5/√(Σ var) | pass |
| Source counts recovered after subtraction (HPGe-like scenario) | generator expectation 400 000 | 5 σ, where σ = 648 counts = 0.16 % of the signal (acceptance window ±0.81 %) | pass: 400 413 ± 648, i.e. +0.64 σ, +0.10 % |
| Coverage of region uncertainty, 300 pseudo-experiments | 68.3 % within 1 σ | ±13.5 % (5 binomial σ) | pass |
| Rate-comparison statistic vs. hand calculation | 100/15 for 2 blocks | exact | pass |
| p-values under the null hypothesis, 300 pseudo-experiments | uniform | fraction < 0.05 within 2…34 of 300 | pass |
| Rate comparison detects a 20 % change of the flat background | p < 1e-6 | – | pass |

Review of an earlier statement (2026-11-11). The Phase 1–4 report said the source counts
were "recovered within 5 σ at better than 1 % sensitivity". The calculation was
reproduced exactly (numbers in the row above). The statement is correct but was loosely
worded: the measured agreement is 0.10 % (0.64 σ), and "1 %" was only an upper bound on
σ/signal. The row now gives the actual figures.

### Peak search and fitting (Phase 5)

Fixed-seed scenarios (`validation/test_peak_fitting.py`; pull = (estimate − truth)/u):

| Check | Tolerance | Result |
|---|---|---|
| All 5 lines of `hpge-isolated-peaks`, `…-no-continuum`, `hpge-high-count`, `nai-isolated-peaks-no-continuum`: area, centroid, FWHM | every pull < 4; fit acceptable | pass (20 lines, 60 pulls) |
| 2 × 10⁶-count line, FWHM 4 channels: width determined to < 0.1 % and unbiased | pull < 4 (a point-sampled Gaussian would be off by ≈ 20 σ) | pass |
| NaI-like scenarios with the schematic Compton continuum (edges inside fit regions) | every line either recovered or reported as POOR FIT; never silently wrong | pass |
| Search finds every true line once; position within max(5u, 0.15 ch); width and area estimates within 15 % | – | pass (5 scenarios) |
| Search on background-only spectra (HPGe-like 8192 ch, NaI-like 1024 ch) | no candidate | pass |
| HPGe-like doublet, 2.0 FWHM apart, 3:1: found as two candidates in one group, fitted, both areas and positions within 4u; ΔBIC supports 2 components | – | pass |
| NaI-like pair 0.9 FWHM apart, 3:1, 4 × 10⁴ counts: search gives **one** candidate; 2-component model statistically supported (ΔBIC > 100) but area correlation < −0.5; statement says "not proof" of resolution | – | pass |
| Single line offered two components | not supported | pass |

Monte Carlo, 1000 independent spectra per case (`scripts/validate_peak_fitting.py 1000`;
definitions in `nsw.services.validation`; the tests repeat the key cases with 300
trials and 5-standard-error tolerances). One Gaussian on a flat continuum, fit region
±3 FWHM, free area, position, width and linear continuum. Ideal: bias 0, pull sd 1,
1 σ coverage 0.683, 2 σ coverage 0.954, p < 0.05 in 5 % of fits.

| Case | Estimator | Area | Continuum /ch | FWHM ch | Failure rate | Area rel. bias ± s.e. | Area pull mean / sd | 1 σ coverage area / position / width | 2 σ coverage area | p < 0.05 |
|---|---|---|---|---|---|---|---|---|---|---|
| HPGe-like, high count | Poisson | 20000 | 50 | 6 | 0.0 % | −0.03 % ± 0.02 % | −0.04 / 0.99 | 0.691 / 0.688 / 0.702 | 0.955 | 0.049 |
| HPGe-like, medium count | Poisson | 500 | 20 | 6 | 0.0 % | +0.06 % ± 0.20 % | −0.01 / 1.00 | 0.694 / 0.683 / 0.668 | 0.955 | 0.044 |
| HPGe-like, low count | Poisson | 30 | 0.6 | 6 | 0.5 % | +1.61 % ± 0.74 % | −0.03 / 1.10 | 0.657 / 0.641 / 0.635 | 0.935 | 0.041 |
| Low count, nearly empty continuum | Poisson | 30 | 0.05 | 6 | 0.0 % | −0.67 % ± 0.60 % | −0.13 / 1.05 | 0.669 / 0.652 / 0.648 | 0.943 | 0.038 |
| NaI-like broad peak, high count | Poisson | 20000 | 200 | 15 | 0.0 % | +0.00 % ± 0.03 % | −0.00 / 0.98 | 0.689 / 0.683 / 0.684 | 0.951 | 0.041 |
| NaI-like broad peak, low count | Poisson | 60 | 2 | 15 | 0.8 % | +2.71 % ± 0.78 % | −0.02 / 1.15 | 0.655 / 0.605 / 0.611 | 0.927 | 0.045 |
| Background-dominated (≈ 3.5 σ peak) | Poisson | 300 | 400 | 6 | 2.3 % | **+6.75 % ± 1.25 %** | −0.10 / 1.26 | 0.623 / 0.588 / 0.591 | 0.909 | 0.038 |
| Least squares, high count | WLS | 20000 | 50 | 6 | 0.0 % | −0.02 % ± 0.02 % | −0.03 / 1.00 | 0.686 / 0.691 / 0.696 | 0.955 | 0.059 |
| Least squares, 8 counts/channel | WLS | 200 | 8 | 6 | 1.4 % | **+2.10 % ± 0.34 %** | +0.18 / 1.11 | 0.611 / 0.648 / 0.624 | 0.919 | **0.135** |
| Poisson, 8 counts/channel | Poisson | 200 | 8 | 6 | 0.0 % | +0.28 % ± 0.31 % | −0.01 / 1.00 | 0.680 / 0.699 / 0.677 | 0.957 | 0.054 |
| Joint gross + background, medium | joint | 500 | 20 | 6 | 0.0 % | +0.34 % ± 0.24 % | +0.02 / 1.02 | 0.663 / 0.679 / 0.690 | 0.955 | 0.032 |
| Joint, low count, equal live times | joint | 30 | 0.6 | 6 | 2.9 % | +1.24 % ± 0.86 % | −0.09 / 1.33 | **0.571 / 0.529 / 0.475** | 0.880 | – |
| Joint, low count, background 10× longer | joint | 30 | 0.6 | 6 | 0.6 % | +1.63 % ± 0.75 % | −0.03 / 1.13 | 0.632 / 0.634 / 0.607 | 0.933 | – |

Reading of the table:

- The Poisson likelihood fit is unbiased with correct coverage at high and medium counts,
  for narrow and broad peaks, and its goodness-of-fit probability stays calibrated down
  to 0.05 counts per channel.
- At about 30 counts per peak, 1 σ intervals cover 0.61–0.67 instead of 0.68 and the area
  is biased by +1 to +3 % (failed fits are excluded, which favours upward fluctuations).
- A marginal peak fitted with free position and width is biased high (+7 %) and
  under-covered. Its position and width should be fixed from calibrations (Phases 6–7).
- Least squares with 1/n weights is biased (+2.1 %) and rejects correct models too often
  at 8 counts per channel; it is offered for net spectra, with a warning on count data.
- The joint fit matches the Poisson fit when the background is well measured and
  understates uncertainties when it is not (a warning is issued).

### Energy calibration (Phase 6)

`validation/test_energy_calibration.py`. Reference energies are synthetic test values.

| Check | Reference | Tolerance | Result |
|---|---|---|---|
| Straight line: coefficients, covariance, χ², residuals | closed-form weighted least squares written out in the test | 1e-9 relative | pass |
| Orders 1–3, 9 points, channels up to 8000 | `numpy.polyfit` (independent implementation) | 2e-6 coefficients, 1e-5 variances | pass |
| Effective variance: channel uncertainty 0.2 at gain 0.5 plus 0.1 keV | √(0.1² + 0.1²) per point; u(E) at the centre = σ/√n; channel uncertainty added in quadrature | 1e-9 | pass |
| Uncertainty of E(ch) from the stored record (powers of channel) vs. the scaled basis, quadratic, channels 150–8500 | equal | 1e-5 | pass |
| One point refused; n = order + 1 fits but cannot be verified; duplicate channel, non-increasing energies, missing uncertainty refused | – | – | pass |
| One line misassigned by 0.3 keV among five | reported as inconsistent; largest normalised residual at the culprit; verification refused; inflation only on request and recorded | – | pass |
| Order test: linear data → quadratic not justified; c₂ = 4 × 10⁻⁷ data → justified and recovered | 5 % | pass |
| Five fitted peaks of `hpge-isolated-peaks` → calibration | generator scale (0.5, 0.25) | pulls < 4; gain to 10 ppm; E(3000) within 4u, u < 0.01 keV | pass |
| Monte Carlo, 600 calibrations, quadratic truth, noise on channels and energies | pulls of c₀, c₁, c₂ and of E(2500): mean 0, sd 1, coverage 0.683; p < 0.05 in 5 % | 5 standard errors | pass |
| Stability statistic | hand calculation (χ² = 2, p = e⁻¹) | exact | pass |

### Resolution and fixed-shape fits (Phase 7)

`validation/test_resolution.py`.

| Check | Reference | Tolerance | Result |
|---|---|---|---|
| Noise-free widths from FWHM² = 0.75 + 0.0025E (and with c = 10⁻⁶) | generating coefficients | 1e-9 (1e-7 with c) | pass |
| Two points (100, 1.0), (1300, 2.0) | hand value a = 0.75, b = 0.0025; prediction at the points has the points' uncertainty; no verification | 1e-10 | pass |
| True a = 0 (NaI-like), one width pushed low | a fixed at 0, reported, no uncertainty, not counted in ndf; b within 1 % | – | pass |
| Widths that fall with x | best admissible model is a constant; reported as poor fit | – | pass |
| FWHM in keV = gain × FWHM in channels | quadrature of both uncertainties | exact | pass |
| Fitted peaks of `hpge-isolated-peaks-no-continuum`, `hpge-high-count`, `nai-isolated-peaks-no-continuum` → model in keV | generator model: predicted FWHM at every line and fitted a, b | 4 u | pass |
| Same data analysed in channel space | same widths after × gain | 4 u | pass |
| Monte Carlo, 600 fits, 6 points with 2 % width noise | pulls of a, b and of FWHM(900 keV): mean 0, sd 1, coverage 0.683; p < 0.05 in 5 % | 5 standard errors | pass |
| Fixed position and width | values held exactly, uncertainty 0, ndf + 2, area within 4 u, explicit notice | – | pass |

Effect of fixing the shape of a weak peak (1000 trials each, `nsw.services.validation`):

| Case | Shape | Failure rate | Area rel. bias ± s.e. | Pull mean / sd | 1 σ / 2 σ coverage |
|---|---|---|---|---|---|
| 300 counts on 400 /ch (≈ 3.5 σ) | free | 2.3 % | +6.75 % ± 1.25 % | −0.10 / 1.26 | 0.623 / 0.909 |
| same | position and width fixed | 0.0 % | +0.13 % ± 0.76 % | −0.00 / 1.00 | 0.676 / 0.959 |
| 30 counts on 0.6 /ch | free | 0.5 % | +1.61 % ± 0.74 % | −0.03 / 1.10 | 0.657 / 0.935 |
| same | position and width fixed | 0.0 % | −0.85 % ± 0.67 % | −0.12 / 1.03 | 0.684 / 0.942 |
| 100 counts on 400 /ch (≈ 1.2 σ) | position and width fixed | 7.6 % | +7.92 % ± 1.94 % | +0.11 / 0.84 | 0.753 / 0.984 |

The last row shows the limit: below about 2 σ many fits end at area = 0 (the constraint
of the Poisson fit), are flagged and excluded, which biases the remaining sample
upwards. Such peaks are non-detections and need a decision-threshold treatment, not a
peak area (not implemented).

### Decay, efficiency and activity (Phase 8; synthetic sources only)

`validation/test_efficiency_activity.py`, `unit/test_efficiency_service.py`. Sources,
emission probabilities, half-lives and the efficiency curve are arbitrary test values
(`nsw.simulation.sources`); none is nuclear reference data.

| Check | Reference | Tolerance | Result |
|---|---|---|---|
| D and f | hand values (0.5; 2; 0.5/ln 2), limits λt → 0 | 1e-9 | pass |
| d ln D/dT, d ln f/dT | central finite differences, three regimes | 1e-5 | pass |
| Simulation and analysis decay law | identical values (independently coded) | 1e-12 | pass |
| Efficiency point | hand: 1000 counts / (0.5 × 1000 Bq × 100 s) = 0.02; three uncertainty groups | 1e-9 | pass |
| One half-life after the reference date; half the live time | efficiency × 2/f; × 2 | 1e-9 | pass |
| Curve through exact model values | model at four energies | 1e-10 | pass |
| Generalised least squares, two sources, all uncertainty groups | independent solution: Cholesky whitening + ordinary least squares | 1e-9 coefficients, 1e-8 covariance | pass |
| Common 5 % source uncertainty | stays 5 % on ε(E) (quadrature with the rest); cancels in the ratio ε(E₁)/ε(E₂) | 1e-6 | pass |
| Inconsistent point (+30 %) | poor fit, largest residual at the culprit, not verifiable | – | pass |
| Relative efficiency | independent of the unknown activity; refused as input to an activity | 1e-12 | pass |
| Activity | hand value; reference time one half-life earlier → × 2/f; statistical and systematic parts | 1e-9 | pass |
| Combination of three lines | exact for exact data; uncertainty below the best single line but above the shared-curve floor; +30 % on one line → inconsistent | – | pass |
| Full chain on generated spectra: fit 8 calibration peaks → curve → fit 3 sample peaks → activity at a reference time 10 days earlier (half-life 30 d) | true efficiency at five energies; true activity 5000 Bq | 4 u; u < 3 % | pass |
| Monte Carlo, 500 trials: calibration counts, sample counts and the certificate value (2 %) all fluctuate | pull of the final activity: mean 0, sd 1, coverage 0.683; consistency p < 0.05 in 5 % | 5 standard errors | pass |
| Service gate | experimental spectrum refused with the list of missing inputs; synthetic spectrum needs a source marked synthetic | – | pass |

Detection probability (candidate within one FWHM of the true position) and false
candidates in peak-free spectra, threshold S ≥ 4, 1000 trials each:

| Area | Continuum /ch | FWHM ch | Scales (ch) | Detection probability ± s.e. | False candidates per 1000 channels |
|---|---|---|---|---|---|
| 20 | 0.6 | 6 | 6, 12 | 0.279 ± 0.014 | 0.000 |
| 30 | 0.6 | 6 | 6, 12 | 0.801 ± 0.013 | 0.000 |
| 50 | 0.6 | 6 | 6, 12 | 1.000 | 0.000 |
| 100 | 20 | 6 | 6, 12 | 0.891 ± 0.010 | 0.010 |
| 200 | 20 | 6 | 6, 12 | 1.000 | 0.010 |
| 300 | 400 | 6 | 6, 12 | 0.566 ± 0.016 | 0.025 |
| 600 | 400 | 6 | 6, 12 | 1.000 | 0.025 |
| 200 | 20 | 15 | 15, 30 | 0.997 ± 0.002 | 0.005 |
| 400 | 20 | 15 | 15, 30 | 1.000 | 0.005 |

These numbers apply to a single Gaussian on a flat continuum. Real spectra contain
Compton edges and other structure that also produce candidates.

NaI(Tl): only the synthetic NaI-like model has been exercised. **There is no
experimental NaI(Tl) validation of any kind.**

### Nuclide candidates (Phase 9; fictitious library only)

`tests/validation/test_identification.py`, `test_review_findings.py`. References: hand
values of the energy window (3σ = 0.1122 keV for u_peak 0.02, u_line 0.01, u_cal 0.03),
a closed form for the two-line intensity test, and the synthetic truth. Checked: the
true nuclide ranks first; one coincident line never exceeds "possible"; a missing
strong line lowers class and score; a wider calibration uncertainty widens the window;
decoys with two coincident lines are separated only with an efficiency curve; relative
intensities give energy evidence only; conflicting and incomplete libraries are
refused or reported. Corrections after the independent review: `docs/REVIEW_PHASE8_9.md`.

### Consolidated run (Phase 10)

`scripts/run_validation.py --trials 1000 --repeat` (about 3.5 minutes) runs every engine
on generated spectra and writes `reports/validation_run.md` and `.json`. Metrics and
criteria are defined in `nsw.services.validation_suite`: relative bias within 5
standard errors, 1σ coverage within 5 binomial standard errors of 0.6827, required
rates exactly met. Run of 2026-11-11, seed 50000, nsw 0.0.1, NumPy 2.5.4:
**24 checked metrics, 0 failed**; digest `54ca82c8…6ab228`.

| Study | Metric | Value | Criterion |
|---|---|---|---|
| Peak search, 200 counts on 20/channel | detection probability | 1.000 | ≥ 0.99 |
| Peak search, peak-free spectra | false candidates per 1000 channels | 0.005 | ≤ 0.1 |
| Peak fit, high count | area relative bias / coverage | +0.004 % / 0.674 | 0 ± 0.12 % / 0.683 ± 0.074 |
| Peak fit, medium count | area relative bias / coverage | −0.02 % / 0.682 | 0 ± 1.0 % / 0.683 ± 0.074 |
| Peak fit, low count, fixed shape | area relative bias / coverage | +1.0 % / 0.682 | 0 ± 3.4 % / 0.683 ± 0.074 |
| Energy calibration, 8 lines | gain relative bias | −2.6 × 10⁻⁸ | 0 ± 2.4 × 10⁻⁷ |
| Energy calibration | gain / offset coverage | 0.663 / 0.659 | 0.683 ± 0.074 |
| Energy calibration | fits with p < 0.05 | 0.038 | 0.05 ± 0.034 |
| Resolution FWHM² = a + bE | a / b coverage | 0.693 / 0.699 | 0.683 ± 0.074 |
| Efficiency curve and activity, full chain (250) | activity relative bias / coverage | +0.008 % / 0.668 | 0 ± 0.08 % / 0.683 ± 0.147 |
| Nuclide candidates with decoys (250) | true nuclide first / decoys accepted | 250 of 250 / 0 | exactly all / none |
| Repeatability | same seed | bit-identical | equal SHA-256 |
| Repeatability | another seed (1050000) | 0 failures | none |

Characterised, not checked (known limitations): free-shape fits of weak peaks are
biased high (+3.4 % ± 0.7 % for 30 counts on 0.6 per channel; +8.5 % ± 1.3 % for 300
counts on 400 per channel, failure rate 2.3 %); a 100-count peak on 20 counts per
channel is found in 90.6 % ± 0.9 % of spectra at threshold 4.

The earlier "5 σ at better than 1 %" statement was reviewed in Phase 5 and reproduced
(400 413 ± 648 against 400 000); it describes one high-count synthetic case and is not
a general sensitivity claim.

## 3. Experimental observations

Not part of this repository. During development the software was also run on private
laboratory HPGe measurements, as a check of file handling and plausibility in channel
units. Those measurements, their description and the tests that read them are not
published, and no experimental validation of any quantity is claimed.

## 4. Not validated

Energy calibration, resolution, efficiency, activity, nuclide identification (not
implemented). Peak areas of real spectra (no reference values exist for this dataset).
The peak shape of real high-statistics HPGe peaks (a tail component is missing).
The energy scale of the real spectra: the calibration engine is validated on synthetic
data only, real calibrations are provisional, and the gain is known to drift (section 3).
Interactive mouse behaviour of the GUI (tested programmatically; checked once by
screenshot on the native Windows platform).
