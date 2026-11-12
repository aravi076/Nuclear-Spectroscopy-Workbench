# Scripts

Run from the project root with the project environment
(`.venv\Scripts\python.exe scripts\<folder>\<script>.py`).

| Folder | Script | Purpose |
|---|---|---|
| `data/` | `generate_synthetic.py` | (re)create the synthetic spectra in `data/synthetic` |
| | `verify_dataset_integrity.py` | create / verify a SHA-256 manifest of a read-only dataset |
| `validation/` | `run_validation.py` | consolidated synthetic validation (many trials, repeatability) |
| | `validate_peak_fitting.py` | peak-fitting validation on synthetic spectra |
| `release/` | `build_windows.ps1` | build the Windows application folder and installer |
| | `smoke_test_installer.ps1` | install / self-test / reinstall / uninstall check of an installer |
| | `collect_licenses.py` | gather third-party licence texts for the installer |
| | `scan_public_copy.py` | privacy scan of a public copy (files and Git history) |
| | `check_links.py` | broken-reference check of the Markdown documentation |
| `dev/` | `make_screenshots.py` | documentation screenshots from synthetic data |
| | `ux_screenshots.py` | interface screenshots for the UX test report |
| | `measure_startup.py` | start-up time measurement |
