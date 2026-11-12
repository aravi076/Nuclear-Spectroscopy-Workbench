# Changelog

## 0.9.0b1 (`v0.9.0-beta`) — 2026-11-12

### Added (beta candidate, 2026-11-12)

- Interactive analysis on the spectrum plot: navigate / zoom-box / region / select modes,
  regions of interest drawn and edited with the mouse, undo/redo, context menu,
  keyboard shortcuts; automatic peak search with candidate classes; region fitting with
  residuals and nested continuum-model comparison.
- Decision thresholds, detection limits and coverage intervals: ISO 11929 framework or
  Currie, selectable; from counts or from the peak fit (Monte Carlo validated; ISO
  conformity not claimed). Optional HPGe low-energy tail model with a bootstrap test.
- Nuclear Data Explorer (provenance, evaluation differences, reference lines on
  calibrated spectra, peak matching with accept/reject); assisted energy calibration.
- Guided workflow panel; Beginner and Advanced modes.
- Project files (schema 2): analyst decisions and regions saved and restored, result
  digests verified, file names instead of full paths.
- Windows installer (per user) with licence texts and synthetic demo spectra; build and
  smoke-test scripts. Dependency lock (`uv.lock`), CI workflow, Dependabot, security
  lint rules, property-based tests, public-copy scanner, link checker.
- Documentation reorganised (`docs/README.md`); interactive code graph (`docs/graph/`).

### Changed

- Start-up about 2.5 times faster (lazy imports); pandas removed.
- Test data no longer reuse file names or acquisition times of private measurements.

### Fixed (independent review, 2026-11-11; `docs/reports/REVIEW_PHASE8_9.md`)

- Nuclide candidates: a rejected candidate no longer downgrades a good one; lines
  outside the efficiency curve carry no weight; two peaks matched by lines of known
  emission probability are required above "possible"; unresolvable peaks count once;
  the anchor peak's area is shared by all lines on it.
- Gates: a calibration verified on synthetic data cannot be attached to an
  experimental spectrum; fitted peaks must belong to the spectrum; the default energy
  range no longer drops the outermost calibration lines.
- Relative-efficiency groups with unrelated normalisation are refused; singular
  covariances give a clear error; non-detections have a reportable activity.
- Evaluation names are stored trimmed and upper-case; non-finite half-lives refused.

### Added

- 15 regression tests for the review findings.
- Consolidated synthetic validation (`scripts/validation/run_validation.py`): checked metrics with
  five-standard-error criteria, characterised limitations, bit-identical repeatability.
- Project files (SQLite): spectra, background designations, calibrations and every result
  with settings and software version; append-only; fits and calibrations recomputed and
  checked when a project is reopened. Project File page and File menu actions.
- Efficiency Calibration, Activity Calculation and Nuclide Identification pages; experimental
  spectra are refused with the list of missing inputs. Generated spectra of the fictitious
  sources SYN-CAL and SYN-X.
- PDF analysis reports and SVG/PDF/PNG figures; identical input gives identical files.
- Windows executable (PyInstaller, one folder) and `--self-test`.
- External dataset survey; post-v1 roadmap.

### Changed

- Fit results record their starting values, so stored fits are reproduced exactly.

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
