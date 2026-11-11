# Changelog

## 0.1.0a1 (v0.1.0-alpha) - 2026-11-11

First public snapshot.

### Added

- Readers for Canberra/Mirion Lynx CSV, Lynx XML (`.ava`) and CAM (`.cnf`) spectra.
- Measurement identity (duplicate exports, intermediate saves) and SHA-256 manifests.
- Detector profiles for HPGe and NaI(Tl).
- Desktop application with seven pages: Project Dashboard, Spectrum Explorer,
  Background Subtraction, Peak Analysis, Peak Fitting, Energy Calibration, Resolution
  Analysis.
- Engines: background subtraction, peak search, peak fitting, energy calibration,
  resolution model, decay corrections, efficiency curve, activity, nuclide candidate
  ranking.
- Synthetic spectrum generator with ground truth; fictitious sources and library.
- 390 tests; validation report.

### Not yet available

- Desktop pages for efficiency, activity and nuclide candidates.
- Project database, reports, Windows executable.
- Nuclear decay data (none is distributed).
