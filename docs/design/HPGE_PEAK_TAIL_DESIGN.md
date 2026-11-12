# HPGe Peak-Tail Model — Design and Validation Plan (R7 A)

Status: **Approved by the owner on 2026-11-12**, including the decisions listed at the end.
Date: 2026-11-12.

## 1. Problem

NSW fits each photopeak as a Gaussian integrated over the channel, on a none / constant
/ linear continuum, with an optional step (`analysis.fitting`). Strong HPGe peaks are
not Gaussian: incomplete charge collection (trapping) and pile-up move counts to the
low-energy side, giving a tail that a Gaussian cannot follow. With high counts the fit
statistic exposes this as a poor fit (small p-value, runs in the residuals), and the
Gaussian under-represents the low-side counts, so the area attributed to the peak
depends on the fit window. Weak peaks are unaffected in practice because the tail is
buried in the Poisson noise.

## 2. Established model forms (prior art)

- Phillips & Marlow's Hypermet function (G. W. Phillips, K. W. Marlow, *Nucl. Instrum.
  Methods* 137 (1976) 525): Gaussian + exponential low-side tail(s) convolved with the
  Gaussian + step + background. The basis of Hypermet-PC
  (https://iki.kfki.hu/nuclear/hypc/index_en.shtml).
- Helmer & Lee, *Nucl. Instrum. Methods* 178 (1980) 499: analytical peak functions for
  Ge detectors with exponential tails.
- Current use: the LEGEND-200 HPGe calibration describes the peak as
  f(E) = N[f_G·g(E) + f_T·t(E) + s(E)] + b(E), with the tail t an exponentially modified
  Gaussian and a step s (arXiv:2605.22479); energy-dependent parameterisations of tail
  and shelf parameters reduce free parameters (Van Gysel et al., X-Ray Spectrom.,
  doi:10.1002/xrs.666).

(Bibliographic details of the 1976 and 1980 papers are to be checked against the
journals before any publication.)

## 3. Proposed model

Per peak j, in channel space, integrated over each channel (as the current model):

```text
m_j(x) = N_j [ (1 − η) G(x; μ_j, σ) + η T(x; μ_j, σ, β) ]  (+ existing optional step)

G : Gaussian bin integral (unchanged)
T : low-side exponentially modified Gaussian, unit area,
    density  t(u) = 1/(2β) · exp(u/β + σ²/(2β²)) · erfc(u/(√2 σ) + σ/(√2 β)),  u = x − μ
    bin integral from its closed-form CDF (difference at the bin edges), no numerical
    quadrature
η ∈ [0, 1) : tail area fraction        β > 0 : tail decay length (channels)
```

- **N_j is the total full-energy area (Gaussian + tail).** Tail counts are full-energy
  events recorded low; excluding them would make the area window-dependent. The same
  definition must be used for efficiency calibration and samples. To enforce it, every
  fit will record its peak model, and the efficiency and activity services will refuse
  to combine efficiency points and sample areas from different peak models (new check,
  part of this work).
- **Shared tail parameters in an ROI**: η and β common to all peaks of a multiplet
  (like the shared width today), because neighbouring peaks have nearly the same
  charge-collection behaviour and separate tails are not identifiable.
- **Energy dependence**: when strong peaks across the spectrum have been fitted with
  free tails, fit η(E) and β(E) as smooth low-order functions (as the resolution model
  is fitted today) and use them as **fixed** shape for weak peaks — the same approach
  NSW already uses for fixed widths.
- Parameter bounds: 0 ≤ η ≤ 0.5, 0.2σ ≤ β ≤ 20σ; at a bound the fit is flagged.

## 4. Statistical questions and how they are handled

| Question | Approach |
|---|---|
| Identifiability: η and β are poorly determined for weak peaks | tail offered only above a count threshold set from the Monte Carlo study (§6), otherwise fixed shape from η(E), β(E) or no tail |
| Correlation of N with η, β | reported (correlation matrix in the fit result); N's uncertainty includes it through the full covariance |
| Initial values | η₀ from the excess of the low-side residuals of the Gaussian fit, β₀ = σ; a small grid of β starts if the fit fails; recorded like other starting values for exact reproduction |
| Model selection Gaussian vs tailed | likelihood ratio Δ = D_gauss − D_tail. The null (η = 0) lies on the boundary and β is undefined under the null, so the usual χ² reference does not apply. Threshold from a **parametric bootstrap** (simulate from the Gaussian fit, refit both, take the 99 % quantile of Δ), tabulated by count level from the Monte Carlo study so a routine fit does not bootstrap |
| Silent use | never: the tail is off by default; the ROI panel shows both fits and the comparison; the simpler model stays unless Δ exceeds the threshold *and* the analyst accepts |
| Effect on the low-count goodness-of-fit test | the existing exact-moment deviance test applies unchanged (two more free parameters) |

## 5. Implementation outline

`analysis.fitting`: new `PeakShape = GAUSSIAN | TAILED` in `FitSettings`; the design
matrix and Jacobian gain the η, β columns (analytic derivatives of the bin integrals);
estimators (Poisson, WLS, joint) unchanged; `FitResult` records the shape, η, β with
uncertainties, and the comparison statistics. `ALGORITHM_VERSION` of fitting bumped.
No change to results of Gaussian fits (regression tests and the validation digest must
be identical with `PeakShape.GAUSSIAN`).

## 6. Validation plan (before any use on real data)

Synthetic generator extension: tailed peaks with known N, μ, σ, η, β on linear
continua (`nsw.simulation`).

| Study | Grid | Metric | Acceptance |
|---|---|---|---|
| Symmetric peaks | N 10²–10⁶, continuum/peak ratio 0–10 | false tail selection rate; area bias of both models | false selection ≤ 1 % at the chosen threshold; Gaussian-model results unchanged |
| Tailed peaks | η 0.02–0.3, β 0.5σ–5σ, N 10³–10⁶ | area bias, pull mean/sd, coverage of 1σ intervals | |bias| < 0.2 σ_N; coverage 0.683 ± 5 SE |
| Gaussian fit of tailed peaks (current behaviour) | same | area bias vs window width | documents the problem quantitatively |
| Weak peaks | N 30–10³ | power of the selection test; behaviour with fixed η(E), β(E) | fixed shape: bias < 0.2 σ_N |
| Doublets | separations 0.5–3 FWHM, ratio 1:1–1:20 | areas, coverage | coverage within 5 SE, else documented limitation |
| Start-value sensitivity | 100 random starts per case | spread of converged N | < 10⁻³ σ_N among converged fits |

Each study is added to the consolidated validation runner with fixed seeds; results go
to `VALIDATION_REPORT.md`. Laboratory spectra are used afterwards only for
plausibility (residual patterns, Δ values) — channels only, never as validation.

## 7. Risks

- Instability for poor starts → bounded parameters, recorded starts, multi-start
  fallback, flagged non-convergence.
- Area/tail correlation inflating uncertainties of moderate peaks → fixed-shape mode.
- Model mis-specification remains (pile-up, high-side tails at high rates are not
  modelled) → stated in the documentation and fit warnings.

## 8. Size

High: about two weeks of focused work including the Monte Carlo study.

## 9. Decisions requested from the owner

1. Approve the model (single shared low-side EMG tail + existing step).
2. Approve the area definition (Gaussian + tail).
3. Approve bootstrap-calibrated model selection with the tail off by default.

## 10. Implementation status (2026-11-12)

Implemented in `nsw.analysis.tailfit` (separate from the Gaussian fit, whose results
are unchanged) and offered in Advanced mode as *Test low-energy tail* on the Regions
panel (background thread). Validated (`tests/validation/test_tailfit.py`): bin shape
equals direct numerical integration of the EMG density (relative 1e-6); recovery of
η, β and area within 5 σ on a strong tailed peak where the Gaussian area is off by more
than 5 σ; Monte Carlo area pulls (150 trials) mean ≈ 0, sd in 0.8–1.25; bootstrap
selection picks Gaussian for a symmetric peak and tailed for η = 0.2. **Not yet done**
from §6: the full grid (weak peaks, doublets, start-value sensitivity), η(E)/β(E)
energy dependence and fixed-shape use for weak peaks, the peak-model check in the
efficiency/activity services, and storing tailed fits in project files. Until then the
tailed fit is informational: the Gaussian fit remains the stored result.
