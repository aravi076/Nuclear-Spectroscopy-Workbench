# NSW v0.9.0-beta (0.9.0b1) — release notes

Date: 2026-11-12. Previous release: `v0.1.0-alpha`.

**What this is:** a beta of the Nuclear Spectroscopy Workbench, a Windows desktop
application and Python library for analysing gamma-ray spectra from HPGe and NaI(Tl)
detectors. Calculations are tested against synthetic spectra with known ground truth.
**Nothing in this release is an experimentally validated measurement result.**

## Install

Download `NSW-Setup-0.9.0b1-x64.exe` and `SHA256SUMS.txt`, check the hash
(`Get-FileHash .\NSW-Setup-0.9.0b1-x64.exe -Algorithm SHA256`), run the installer
(per user, no administrator rights). The installer is **not code-signed**: Windows
SmartScreen warns; choose *More info → Run anyway* only if the hash matches. Start menu:
*NSW*, *NSW self-test*, *Demo spectra (synthetic)*, *Licences*. Details:
`docs/user/WINDOWS_INSTALLER.md`. From source: Python 3.12 or later, `uv sync --locked
--all-extras`.

## New since v0.1.0-alpha

- **Interactive analysis on the spectrum:** navigate / zoom-box / region / select modes,
  regions of interest drawn and edited with the mouse, undo/redo, context menu, keyboard
  shortcuts; automatic peak search with candidate classes (strong, weak, doubtful, not
  photopeak-like); fitting a region with residuals; nested continuum-model comparison.
- **Decision thresholds, detection limits, coverage intervals:** ISO 11929 framework or
  Currie, selectable; from counts and from peak fits (Monte Carlo validated).
- **Optional HPGe low-energy tail model** with a bootstrap-calibrated test (Advanced mode).
- **Nuclear Data Explorer:** libraries with full provenance, evaluation differences,
  reference lines on calibrated spectra, peak-to-line matching with accept / reject.
- **Assisted energy calibration:** suggested line assignments from an approximate gain,
  checks for duplicates, coverage and outliers.
- **Guided workflow panel** and **Beginner / Advanced modes**.
- Pages and tools from Phases 10–12: efficiency and activity (synthetic sources only),
  nuclide candidates, project files, PDF reports, consolidated validation.
- **Project files:** analyst decisions and regions saved and restored; result digests
  verified on reopening; file names only (no full paths).
- **Windows installer** with licence texts (incl. LGPL-3.0 for Qt) and demo spectra.
- Engineering: locked dependencies, CI on Windows and Linux, security lint rules,
  property-based tests, about 2.5× faster start-up, documentation reorganised,
  interactive code graph (`docs/graph/`).

## Known limitations

- Measured spectra: energy calibration stays provisional; efficiency, activity and
  definite identification are refused (certified source data and geometry needed).
- Detection limits follow the ISO 11929 framework; conformity with the standard is not
  verified. The tail model is informational and partly validated.
- No real nuclear data are shipped; DDEP/ENSDF files can be imported with provenance.
- The desktop interface needs Python 3.12+ when run from source (the installer bundles it).
- Not code-signed; tested on the development PC — see
  `docs/user/WINDOWS_INSTALLER_VALIDATION.md` for what has and has not been tested.

## Checks for this release

558–559 automated tests (unit, interface with synthesised mouse/keyboard input,
validation) on Windows (Python 3.12) and Linux (Python 3.12; engine also on 3.11) in CI;
1000-trial synthetic validation with bit-identical repeatability; installer install /
self-test / reinstall / uninstall smoke test.
