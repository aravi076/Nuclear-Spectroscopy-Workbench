# Scientific Methodology

Covers what is implemented (Phases 1–4 and the synthetic generator). Each section gives
the mathematics, the assumptions and the limitations. Later phases append sections.

## 1. Spectrum model and counting statistics (`nsw.core.spectrum`)

A pulse-height spectrum is a vector of counts N_i, i = 1…n (1-based channels, matching
the instrument). Each measured N_i is treated as an independent Poisson variate with
expectation μ_i; the variance estimate used is the plug-in value var(N_i) ≈ N_i.

- *Count data* (kinds RAW, BACKGROUND): non-negative integers, Poisson statistics.
- *Derived data* (NET, PROCESSED): real values that may be negative, with an explicit
  per-channel variance and a recorded processing history.

Limitations: the plug-in variance is poor for small N and is zero for N = 0. Sums over
many channels are fine; single low-count channels must be treated with Poisson
likelihood methods (planned for peak fitting). Channel-to-channel correlations are not
modelled (none exist in raw data; smoothing introduces them, see section 5).

Dead time: the fraction 1 − t_live/t_real is reported, not corrected for; live time is
the divisor for every rate. When a file stores times rounded to whole seconds
(`time_quantum_s = 1`) the dead-time figure is flagged unreliable.

## 2. Units and uncertainties (`nsw.core.units`)

`Quantity(value, unit, stat, sys)` keeps the statistical (counting) and systematic
standard uncertainties apart; the combined standard uncertainty is
u = √(stat² + sys²). Helper arithmetic (`add_independent`, `sub_independent`,
`ratio_independent`) applies first-order propagation component-wise and is valid **only
for independent operands**. Correlated inputs require covariance-aware propagation
(Phase 8). Values are printed rounded to two significant digits of the uncertainty.

## 3. Energy calibration status (`nsw.core.calibration`)

No calibration is fitted yet. A calibration record has an origin
(`imported-vendor`, `fitted`, `assumed`, `synthetic-truth`) and a status
(`unverified`, `verified`). Rules enforced by the data model:

- vendor and assumed calibrations can never be `verified`;
- a `verified` fitted calibration needs reference points and a covariance matrix;
- `Spectrum.energies()` raises unless the spectrum carries a verified calibration.

The calibration stored in the files of the development dataset is wrong, so all
experimental spectra are `UNVERIFIED`
and every display and export uses the channel axis.

## 4. Measurement identity (`nsw.io.identity`)

- **Snapshot**: files whose channel data are identical (SHA-256 of the count array).
- **Acquisition**: snapshots whose start times agree within 1 s. Ordered by live time,
  if every channel's count is non-decreasing from each save to the next, the saves are
  cumulative states of one running acquisition; only the last is an independent
  measurement, the others are `intermediate-save`.

Justification: two different acquisitions producing identical 8192-channel count
vectors, or one acquisition's counts decreasing, are both impossible for an accumulating
MCA memory; a shared start time is how the instrument labels one acquisition.

Limitations: an acquisition that was stopped, saved and *restarted with a new start
time* cannot be linked to its first part. Files without a start time are each treated as
their own acquisition. Where copies of the same data disagree on the start time, the CNF
record is used and the disagreement is reported.

## 5. Background subtraction (`nsw.analysis.background`)

For a gross spectrum G (live time t_G) and an **independent** background spectrum B
(live time t_B) from the same detector at the same gain, with α = t_G / t_B:

    N_net,i  = G_i − α · B_i
    σ²_net,i = G_i + α² · B_i

The variance follows from var(aX + bY) = a² var X + b² var Y for independent X, Y with
Poisson variances estimated by the observed counts. Live times are used so the
instrument's dead-time correction enters exactly once.

- Negative net values are kept: clipping would bias every sum over a weak region upward.
- If a live time is rounded to a step q, its standard uncertainty is q/√12 (uniform
  error). The resulting uncertainty of α is common to all channels (fully correlated),
  so it is **not** added to σ²_net,i; for a sum over a region it contributes
  u_sys = u(α) · Σ B_i, reported separately from the Poisson part √(Σ σ²_net,i).

Validity checks (the calculation is refused if any fails): both inputs are measured
counts; the background has been explicitly designated; equal channel range; live times
present; the two are not the same acquisition (identical data or start time); the
background is not an intermediate save; detector identifiers do not differ;
gain-determining hardware settings (conversion gain, coarse/fine gain, HV), where both
files record them, do not differ; experimental and synthetic data are not mixed.

Not verifiable from metadata, therefore stated as an assumption in every result:
identical shielding and surroundings, and identical gain where hardware settings are
not recorded (CSV and CNF files). No gain-drift check exists yet (needs peak fitting).
Losses not handled by the live-time clock (pile-up at high rate) are not corrected; a
warning is issued above 10 % dead time.

### Comparison of two independent backgrounds

Channels are summed in blocks of 64. Under the hypothesis of equal count-rate spectra,
conditional on the block total n_k = A_k + B_k, the count A_k is binomial with
p = t_A/(t_A + t_B). The statistic

    χ² = Σ_k (A_k − p n_k)² / (n_k p (1 − p))

is compared with a χ² distribution with one degree of freedom per block (p is fixed by
the live times, no parameter is fitted). Blocks with n_k < 20 are excluded because the
normal approximation fails there. The test is refused for dependent spectra.

Limitations: it tests the whole channel range including the low-channel electronic
noise region; a small p-value shows a difference but not its cause.

### Display smoothing

`smooth_for_display` is a moving average for viewing only. It correlates neighbouring
channels and distorts peak shapes; its output has kind PROCESSED, is flagged
`display_only`, and is rejected as input by the subtraction code.

## 6. Synthetic spectra (`nsw.simulation`)

Purpose: to test numerical recovery of known parameters. The model is intentionally
simple and is **not** a model of the laboratory detector.

- Energy scale E(ch) = c₀ + c₁·ch; channel ch integrates [E(ch−½), E(ch+½)).
- Full-energy peak: Gaussian in energy with σ = FWHM(E₀)/(2√(2 ln 2)),
  FWHM(E) = √(a + bE + cE²). The expectation per channel is the exact integral
  A·[Φ((E_hi−E₀)/σ) − Φ((E_lo−E₀)/σ)], not the density at the channel centre.
- Continuum per line: a schematic flat shelf from 0 to the Compton edge
  E_C = 2E₀²/(m_e c² + 2E₀), smeared at the edge with the local resolution, containing
  `continuum_ratio` × peak area counts. Not a Klein–Nishina shape; no escape,
  backscatter or summing features. m_e c² = 510.99895 keV (CODATA 2018).
- Background: rate per channel = constant + amplitude·exp(−E/decay).
- Each component is sampled independently from a Poisson distribution with
  `numpy.random.Generator(PCG64(seed))`; realised and expected counts of every component
  are stored in the ground-truth record together with all assumptions.
- No dead time, pile-up or gain drift.

Assumed scenario parameters (illustrative, not measured): HPGe-like 8192 channels,
0.25 keV/channel, FWHM² = 0.75 + 0.0025·E; NaI-like 1024 channels, 3 keV/channel,
FWHM² = 3.24·E (7 % at 662 keV). Line energies (100, 300, 600, 1000, 1500 keV and the
doublets) are arbitrary test values, not nuclear data.

Reproducibility: identical for a given specification, seed, generator version and NumPy
version (NumPy does not guarantee identical random streams across versions; the NumPy
version and the data hash are stored in each file).

## 7. Peak search (`nsw.analysis.peaks`)

The spectrum y is correlated with a zero-area, symmetric kernel, the negative second
derivative of a Gaussian of standard deviation s (the detection scale):

    k_j = (1 − j²/s²) exp(−j²/2s²),  j = −h…h,  h = ⌈4s⌉,  then k ← k − mean(k)
    R_i = Σ_j k_j y_{i+j}        V_i = Σ_j k_j² var(y_{i+j})        S_i = R_i / √V_i

Zero area and symmetry make R vanish for any constant or linear continuum, so no
baseline has to be estimated. A candidate is a local maximum of the significance S above
the threshold (default 4). Several scales can be searched; where candidates coincide the
most significant is kept. Candidates closer than 3 FWHM form one *fit group*.

For a Gaussian peak of area N and standard deviation p, R(s) = N s³ / (s² + p²)^{3/2}.
The responses at s and s/2 therefore give baseline-free estimates of the width and the
area; the position is the vertex of a parabola through three values of R, with an
uncertainty propagated from the channel variances. These are estimates for display and
for starting a fit. On a continuum-dominated background S is largest for s = √5·p, so
the scale that detects a peak is not a width estimate and a coarse ladder of scales
suffices.

Limitations:

- V uses the plug-in variance (observed counts). Where channels are mostly empty S is not
  normally distributed, and where all channels under the kernel are empty S is defined
  as zero. No pseudo-counts are added. A warning is issued when more than 20 % of the
  searched channels are empty.
- Any sharp curvature of the continuum (Compton edge, backscatter bump) responds. A
  candidate is **not** a confirmed full-energy peak.
- Lines closer than about one FWHM give a single candidate.
- Positions are channels. An energy is attached only with a verified calibration.

## 8. Peak fitting (`nsw.analysis.fitting`)

**Model.** A spectrum is a histogram, so a Gaussian line contributes to channel x the
integral over the channel,

    p(x) = N [Φ((x + ½ − μ)/σ) − Φ((x − ½ − μ)/σ)],

with fitted parameters area N, centroid μ and standard deviation σ. Evaluating the
density at the channel centre instead biases σ by 1/(24σ²) relative (1.4 % at a FWHM of
4 channels), far more than the statistical uncertainty of a strong peak. Derived:
FWHM = 2√(2 ln 2)·σ = 2.35482 σ and the height A = N/(σ√(2π)) of
G(x) = A exp(−(x−μ)²/2σ²), with its uncertainty from the covariance of N and σ.
Continuum: none, constant, or linear (parameterised by its values at the two ends of the
region). Optional step: f·A_j·[1 − Φ((x − μ_j)/σ_j)] per peak with one common fraction
f ≥ 0, to be used only where physically justified. A multiplet is a sum of such
Gaussians, by default with one common σ.

**Estimators.**

| Name | Statistic minimised | Use | Constraints |
|---|---|---|---|
| Poisson likelihood | D = 2 Σ [m − n + n ln(n/m)] (the n ln term is 0 for n = 0) | measured counts | areas ≥ 0, continuum ≥ 0 |
| Weighted least squares | χ² = Σ (y − m)²/var | net (subtracted) spectra, whose values may be negative | none; refused if any variance is 0 |
| Joint gross + background | profile deviance of G ~ Poisson(αβ + s), B ~ Poisson(β) | gross spectrum with an independent designated background | areas ≥ 0; source continuum free |

- The Poisson likelihood is refused for net data: it is not defined for negative values
  and net values are not Poisson distributed.
- Empty channels need no special treatment in the likelihood (they contribute 2m) and no
  pseudo-count is ever added. Least squares cannot use a zero-variance channel, and with
  1/n weights it is biased at low counts (section 2 of VALIDATION_REPORT.md: +2.1 % in
  area at 8 counts per channel, where the likelihood fit is unbiased).
- In the joint fit the background expectation β_x of every channel is a nuisance
  parameter eliminated analytically: β is the larger root of
  α(1+α)β² + [(1+α)s − α(G+B)]β − Bs = 0. No subtraction is formed. The source term s
  may be negative as long as αβ + s ≥ 0, so the source continuum is not forced to be
  positive (forcing it biased the peak area by −2.3 % in a test with zero true
  continuum). Known weakness: with fewer than about 5 background counts per channel and
  a background measured no longer than the sample, the profile likelihood understates
  the uncertainties (1 σ coverage 0.47–0.57 instead of 0.68); a warning is issued.

**Minimisation.** Damped Fisher scoring (Levenberg–Marquardt on the likelihood) with an
active set for the bounds; analytic Jacobian, verified against finite differences.
Convergence: expected remaining decrease of the statistic below 10⁻⁷.

**Covariance.** Inverse of the expected Fisher information JᵀWJ at the solution, with
W = 1/m (Poisson), 1/var (least squares) or 1/(μ_G + α²β) (joint; this is the
information left after eliminating β). Uncertainties are **not** multiplied by
√(χ²/ndf). A parameter on a bound has no uncertainty and the fit is flagged, except for
a continuum or step that is zero within its constraint (reported as a caution).

**Goodness of fit.** Pearson χ² and ndf are always given. Its χ² probability is used
when all fitted expectations are ≥ 5. Otherwise χ² tables do not apply (the deviance of
a channel with expectation m has mean ≈ 2m, not 1, for small m): the deviance is then
compared with its exact Poisson mean and variance, summed over channels (normal
approximation). For the joint fit no probability is quoted at low counts. A probability
below 10⁻³ marks the result **POOR FIT**: the model does not describe the data and the
parameters are not reliable. A Wald–Wolfowitz runs test on the residual signs is
reported as a check for structure.

**What a converged fit does not show.**

- Significance: a component with area/uncertainty below 3 is reported as not detected.
- Resolution of a multiplet: `multiplet_evidence` compares the k-component fit with the
  best (k−1)-component fit by ΔAIC and ΔBIC. The likelihood-ratio statistic has no
  χ² distribution here (boundary of the parameter space), so no p-value is quoted.
  "Supported" requires ΔBIC ≥ 10 and an unflagged, statistically acceptable full fit,
  and is worded as statistical support for the model, never as proof of resolved lines.
  The separation in FWHM and the correlation of the areas are reported alongside.
- Real HPGe peaks with many counts have a low-energy tail that this model does not
  contain. Such fits are reported as poor; a tail component is future work.
- Weak peaks fitted with free position and width are biased high (+7 % for a 3.5 σ peak
  in the Monte Carlo study) because the fit follows fluctuations. Fixing position and
  width from calibrations becomes possible in Phases 6–7.

## 9. Energy calibration (`nsw.analysis.energy_calibration`)

**Model.** E(ch) = Σ_{j=0}^{order} c_j ch^j (keV, 1-based channels), order 1 by
default, 2 or 3 only when justified. Each reference point has a fitted peak position
ch_i ± u(ch_i) and a reference energy E_i ± u(E_i).

**Regression.** Both uncertainties enter through the effective variance

    v_i = u(E_i)² + [E′(ch_i)]² u(ch_i)²,

iterated until the coefficients are stable. The coefficients minimise
χ² = Σ (E_i − E(ch_i))²/v_i; covariance C = (AᵀWA)⁻¹. The regression is computed in the
variable t = (ch − mean)/half-span and transformed exactly to powers of ch, so the
normal equations stay well conditioned for channels of several thousand. Checked
against the closed-form straight-line formulas and against `numpy.polyfit`.

**Uncertainty of a calibrated energy.** u²(E(ch)) = gᵀCg with g = (1, ch, ch², …),
plus [E′(ch)·u(ch)]² when the position itself is uncertain. This describes the fitted
curve only: it contains no allowance for model error and none for gain changes after
the calibration measurement.

**Rules.**

- One peak never calibrates: it cannot give gain and offset.
- order + 1 points give a curve through the points with no check (ndf = 0). Such a fit
  is allowed for orientation but can never be VERIFIED; verification needs at least
  order + 2 points (also enforced in the calibration record itself).
- Every reference energy must name its source. NSW supplies no reference energies.
- The calibration is supported by data only between the lowest and highest reference
  channel (the *valid range*); energies outside are flagged as extrapolated.
- Inconsistent points (probability below 10⁻³) are reported, with the normalised
  residuals pointing at the culprit, and block verification. The covariance is
  multiplied by χ²/ndf only at the analyst's explicit request, which is recorded in the
  result and in the record.
- A higher order is "justified" only if its highest coefficient differs from zero by at
  least three standard uncertainties in a fit with at least one redundant point.
- **Experimental spectra:** a calibration is stored as UNVERIFIED ("provisional") until
  the laboratory confirms the line assignments and a traceable source of the reference
  energies. Spectra then stay in channel units. Synthetic spectra can be verified.

**Stability.** `position_stability` tests whether the fitted positions x_i ± u_i of one
peak in several *independent* acquisitions are consistent with a common value:
χ² = Σ (x_i − x̄)²/u_i² about the weighted mean, ndf = n − 1. Intermediate saves are
excluded. The test compares positions; that they belong to the same line is the
analyst's responsibility. Where a gain drift is found: calibrate per run or per short time window; a calibration has a
validity in time as well as in channel range; background subtraction between distant
runs violates its equal-gain assumption at background lines.

## 10. Resolution (`nsw.analysis.resolution`)

**Quantities.** FWHM in channels comes from the peak fit. FWHM in keV = gain × FWHM in
channels, with the local gain E′(ch) of a verified calibration. Relative resolution =
FWHM(E)/E. Without a verified calibration only the channel quantities exist.

**Model.** FWHM(x)² = a + b·x + c·x², a, b, c ≥ 0 (a: electronic noise; b: statistics
of charge or light production; c: incomplete charge collection or non-uniformity). x
is the energy in keV, or the channel number for uncalibrated spectra; for a linear
energy scale the form is the same in both variables. The same functional form serves
HPGe and NaI(Tl); what differs is the size of the terms (the synthetic HPGe-like model
has a > 0 and b small, the NaI-like model a = 0 and b large), and each detector gets its
own fitted model. The c term is fitted only on request.

**Estimation.** Weighted least squares on the widths themselves,
χ² = Σ (W_i − FWHM(x_i))²/u_i², not on W², because squaring a noisy width biases it
upwards by u². The non-negativity constraints are treated exactly by fitting every
subset of the requested terms and keeping the admissible solution of lowest χ². A term
whose unconstrained estimate is not positive is fixed at 0, reported as not supported by
the data, has no uncertainty and is not counted as a parameter. Covariance (JᵀWJ)⁻¹ of
the retained terms; the uncertainty of a predicted width is gᵀCg with
g = (1, x, x²)/(2·FWHM). Predictions outside the range of the points are flagged.

**What may enter.** Only widths of statistically acceptable fits whose width was
fitted, and only single full-energy lines: a multiplet, an annihilation peak or any
otherwise broadened peak is not a resolution measurement. NSW cannot know this; the
analyst unticks such peaks, helped by the normalised residuals (a broadened peak stands
out and makes the fit poor).

**Status.** A model in keV from a synthetic spectrum with at least one redundant point
and an acceptable fit is verified. Models in channel units are tied to an unverified
energy scale and are always provisional, as are all experimental models at this stage.

**Use: peaks of known shape.** `FitSettings(fixed_centroids=…, fixed_width=…)` holds
positions and/or widths at supplied values. A weak peak fitted with a free shape follows
fluctuations: in the Monte Carlo study a 3.5 σ peak is biased by +6.8 % with 62 %
coverage; with position and width fixed the bias is +0.1 % ± 0.8 % and the coverage
68 %. The uncertainty of the fixed values is not propagated into the area; this is
stated with every such result. For peaks below about 2 σ the constraint area ≥ 0 biases
the *reported* areas upwards because fits ending at zero are flagged and left out.

## 11. Decay, efficiency and activity (`nsw.analysis.decay`, `efficiency`, `activity`)

**Status: engines validated on synthetic sources only.** No efficiency and no activity
is computed for the laboratory data; the service layer refuses experimental spectra and
lists the missing inputs.

**Decay** (half-life T, λ = ln 2/T). D(Δt) = exp(−λΔt) relates the activity at a
reference time to the activity Δt later. f(t) = (1 − e^{−λt})/(λt) is the mean activity
during a measurement of real time t relative to its start. Real time is used because
decay does not pause during dead time. Single nuclide only: no ingrowth, no chains.

**Efficiency from a line of a reference source.**

    N = ε · P_γ · A₀ · D · f · t_live        ε = N / (P_γ A₀ D f t_live)

N net peak area, P_γ photons per decay, A₀ activity at its reference time, t_live live
time. Live time enters once; the instrument's dead-time correction is never applied a
second time. Assumption: counting losses are spread evenly over the measurement.
Relative uncertainties are kept in three groups: statistical (peak fit), line (P_γ,
independent between lines), source (activity and half-life, common to all lines of a
source and therefore fully correlated between them).

**Which efficiency.** What a reference source of known activity yields is the *absolute
full-energy-peak efficiency* for that one geometry: counts in the peak per photon
emitted. It is the product of a geometric part (fraction of photons reaching the
detector) and an intrinsic part (counts per incident photon). NSW does not separate
them: that needs the geometry, which is recorded as a description but never assumed.
*Relative efficiency* (energy dependence only, arbitrary units) is obtained from lines
of one nuclide as N/P_γ and needs no activity; it cannot give an activity.

**Curve.** ln ε(E) = Σ_k a_k [ln(E/E₀)]^k, E₀ the geometric mean of the calibration
energies. Generalised least squares in the logarithm with the full covariance of the
points, V_ij = δ_ij(stat_i² + line_i²) + [same source]·source_i·source_j. A 5 % activity
uncertainty common to all points therefore remains 5 % on the curve (it is not averaged
away) but cancels in the ratio of efficiencies at two energies. Coefficient covariance
(XᵀV⁻¹X)⁻¹; relative uncertainty of ε(E) = √(gᵀCg). Valid between the lowest and the
highest calibration energy; extrapolation is flagged.

**Activity.** A_ref = N / (ε(E) · P_γ · t_live · f · D), with the reference time stated
(default: start of the measurement). Several lines of one nuclide share the curve and
the half-life, so they are combined in the logarithm by generalised least squares with
V_ij = δ_ij(stat_i² + line_i²) + C_ε(E_i, E_j) + h_i h_j u(T)². The χ² of the lines about
their mean tests their consistency (interfering peak, wrong emission probability,
summing, wrong nuclide). Statistical and systematic parts are reported separately.
Specific activity = A/m with the mass uncertainty added to the systematic part. A
negative net area gives a negative activity estimate for a single line (kept, unbiased);
such lines are not combined in the logarithm.

**Not corrected, stated with every result:** true coincidence summing, self-attenuation
in extended sources, and any difference between calibration and sample geometry.

## 12. Nuclide candidates (`nsw.analysis.identification`)

The complete rules are the module docstring; in short. A peak matches a library line
when |E_peak − E_line| ≤ k·√(u_peak² + u_line² + u_cal² + u_extra²), k = 3 by default
(a true line is lost once in 370; the window and the chance coincidences grow with k).
Per nuclide the matched lines, the expected but missing lines and the shared peaks are
listed. With an efficiency curve the area of an unmatched line is predicted from the
anchor (the most significant matched peak) and compared with an approximate Currie
limit, and the values ln(A/(P·ε)) of the matched lines are tested for agreement by
generalised least squares. Classes: *not supported*, *possible*, *probable*,
*consistent*; one matched peak is at most *possible*, *consistent* needs an efficiency
curve. The output is a list of candidates with evidence, never an identification, and
the service refuses spectra without a verified energy calibration and every
experimental spectrum while experimental verification is disabled. Nuclear data are
not shipped (`docs/science/nuclear_data.md`). Known weaknesses: `docs/reports/REVIEW_PHASE8_9.md`.

## 13. Validation metrics (`nsw.services.validation_suite`)

Relative bias, pull, coverage, rates and repeatability are defined in the module
docstring and applied with a tolerance of five standard errors; metrics without an
ideal value are reported as *characterised* and never as passed.

## 14. Not yet implemented

Decision thresholds and detection limits, coincidence-summing and attenuation
corrections, Monte Carlo uncertainty propagation, nuclide identification.
Absolute efficiency and activity additionally require source certificates and geometry
that the laboratory dataset does not contain.
