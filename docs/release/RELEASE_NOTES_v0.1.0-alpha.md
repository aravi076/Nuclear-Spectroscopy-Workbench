# NSW v0.1.0-alpha

First public snapshot. **Alpha: for evaluation of the code and methods, not for
producing measurement results.**

## Included

- Import of Canberra/Mirion Lynx CSV, Lynx XML (`.ava`) and CAM (`.cnf`) spectra,
  chosen by file content; read-only.
- Measurement identity (duplicate exports, intermediate saves of one acquisition) and
  SHA-256 integrity manifests.
- Desktop application (PySide6, PyQtGraph) with seven pages: Project Dashboard, Spectrum
  Explorer, Background Subtraction, Peak Analysis, Peak Fitting, Energy Calibration,
  Resolution Analysis.
- Engines with tests: background subtraction, peak search, peak fitting (Poisson
  likelihood, least squares, joint gross + background), energy calibration, resolution
  model, decay corrections, efficiency curve, activity, nuclide candidate ranking.
- Synthetic spectrum generator with ground truth (HPGe-like and NaI-like scenarios).

## Not included

- Desktop pages for efficiency, activity and nuclide candidates (engines only).
- Project database, reports, Windows installer.
- Any nuclear decay data. The identification engine is tested with a fictitious
  library; real data must be imported by the user.
- Any laboratory measurement.

## Verification (2026-11-11, Windows 11, CPython 3.12)

- 405 tests pass.
- 97 % coverage of non-GUI code; Ruff and strict MyPy clean.
- Validation against hand calculations, independent numerical solutions and Monte
  Carlo ensembles of synthetic spectra: `VALIDATION_REPORT.md`.

## Known limitations

- All validation is synthetic; no result is experimentally validated.
- Measured spectra used in development have an unverified energy calibration and are
  handled in channel units; efficiency and activity are blocked for them.
- Gaussian peak model without low-energy tail: strong HPGe peaks are flagged as poor
  fits.
- Weak peaks with free position and width are biased high; no detection limits.
- No gain-drift model; no coincidence-summing or self-attenuation correction.
- Results are not saved between sessions.
- The interface is tested programmatically; mouse-driven testing is incomplete.

## Installation

See `README.md`. Python 3.11 or later on Windows.
