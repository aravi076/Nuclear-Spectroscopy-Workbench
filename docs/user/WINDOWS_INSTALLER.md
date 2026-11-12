# Windows Installer

NSW is distributed for Windows 10/11 x64 as a per-user installer built from a
PyInstaller one-folder application. Users need no Python, Git, uv or other software.

## For users

1. Download `NSW-Setup-<version>-x64.exe` and `SHA256SUMS.txt` from the GitHub release.
2. Optional, recommended: check the download in PowerShell —
   `Get-FileHash .\NSW-Setup-<version>-x64.exe -Algorithm SHA256` must equal the value in
   `SHA256SUMS.txt`.
3. Run the installer. It installs for the current user only and does not need
   administrator rights (default folder `%LOCALAPPDATA%\Programs\NSW`). The installer
   is **not code-signed**: Windows SmartScreen shows "Windows protected your PC";
   choose *More info → Run anyway* only if the checksum matched.
4. Start NSW from the Start Menu. The Start Menu folder also has *NSW self-test* (runs
   the built-in end-to-end test on generated spectra and writes
   `Documents\NSW self-test\selftest.log`), *Demo spectra (synthetic)* and *Licences*.

What the installer puts on the machine: the program folder, Start Menu entries, an
optional desktop shortcut, and an uninstaller (*Settings → Apps* or the Start Menu).
Upgrading: run a newer installer; it replaces the program in place.

**Your data is never removed** by an upgrade or by uninstalling: settings in
`%LOCALAPPDATA%\NSW` (`local.yaml`, see `config/nsw.example.yaml`), and every project,
report, export or self-test folder you created.

Demonstration data: generated spectra of fictitious sources (HPGe-like and NaI-like),
clearly marked *synthetic*. No laboratory measurement and no real nuclear data are
included.

## For maintainers: building

Requirements on the build machine: the locked development environment (`uv`), Inno
Setup 6 (`ISCC.exe`, per-user install is fine).

```powershell
.venv\Scripts\python.exe -m pytest            # must pass first
powershell -ExecutionPolicy Bypass -File scripts\release\build_windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\release\smoke_test_installer.ps1 build\installer\NSW-Setup-<version>-x64.exe
```

`build_windows.ps1`: `uv sync --locked`; `scripts/release/collect_licenses.py` (licence files
of every distributed package, LGPL-3.0 and GPL-3.0 texts for Qt for Python, the Python
licence, the Qt replacement notice); PyInstaller with `packaging/windows/NSW.spec`;
self-test of the built folder; Inno Setup with `packaging/windows/installer.iss`;
`SHA256SUMS.txt`.

`smoke_test_installer.ps1`: silent install into a temporary folder, presence of
program, licences and demo data, self-test of the installed program, reinstall
(upgrade path), silent uninstall, and checks that program files are gone while user
data and self-test output remain.

## Licences in the distribution

NSW: MIT. Qt for Python (PySide6, Shiboken6) and Qt: LGPL-3.0 — the one-folder layout
keeps the Qt libraries as separate, replaceable files (`QT-LGPL-NOTICE.txt`). All other
packages: their own licences, listed in `_internal\licenses\THIRD_PARTY_LICENSES.md`.
The PyInstaller bootloader is GPL-2.0 with an exception for distributed applications.
Inno Setup's licence permits distributing the installers it creates.

## Not provided

- Code signing (no certificate exists; nothing is signed or claimed to be signed).
- An SBOM file (planned with a release workflow).
- Automatic updates.
