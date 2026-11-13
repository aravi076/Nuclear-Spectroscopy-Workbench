# NSW v0.9.0-beta.2 (0.9.0b2) — release notes

Date: 2026-11-13. Previous release: `v0.9.0-beta` (0.9.0b1).

A bug-fix beta after the first hands-on test with a measured spectrum. Calculations are
tested against synthetic spectra with known ground truth. **Nothing in this release is
an experimentally validated measurement result.**

## Install

**Windows installer:** download `NSW-Setup-0.9.0b2-x64.exe` and `SHA256SUMS.txt`, check
the hash (`Get-FileHash .\NSW-Setup-0.9.0b2-x64.exe -Algorithm SHA256`), run the
installer (per user, no administrator rights). It is **not code-signed**: Windows
SmartScreen warns; choose *More info → Run anyway* only if the hash matches. Installing
over 0.9.0b1 upgrades it; settings are kept.

**One command, with uv:**
`uv tool install --python 3.12 "nsw[gui] @ git+https://github.com/aravi076/Nuclear-Spectroscopy-Workbench@v0.9.0-beta.2"`,
then `nsw`. Details: `docs/user/installation.md`.

## Fixed

- Peak search: the step at the lower-level discriminator of measured spectra is no
  longer reported (and fitted) as a peak.
- Reports: fixed peak positions and widths read "fixed" instead of "± 0.000"; a peak
  fitted again in an overlapping region is listed once (the latest fit); the file's
  folder is not printed (file name and SHA-256 identify it); long labels are no longer
  cut off; instrument settings stored as 32-bit numbers are shown at their precision.

## Added

- One-command installation with `uv tool install` (tested: self-test 11 of 11 steps).

## Known limitations

Unchanged from `v0.9.0-beta`; see its release notes and
`docs/user/WINDOWS_INSTALLER_VALIDATION.md` (the installer is tested on the development
PC; a test on a machine without Python is still open).
