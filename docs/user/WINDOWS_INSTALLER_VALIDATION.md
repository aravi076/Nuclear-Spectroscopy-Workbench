# Windows Installer Validation

## 1. Development machine (done 2026-11-12)

Machine: the development PC (Windows 11 Home x64; Python, Git and uv installed — so this
is **not** a clean-machine test). Installer: `NSW-Setup-0.9.0.dev0-x64.exe`, 74.8 MB,
SHA-256 `5902ae0ea1918307b7ffb6469fb44273ab4f312eba851396019762410d0b8767`, built
by `scripts/release/build_windows.ps1` from commit `163d3b0` (later commits changed only documentation paths in comments and docstrings;
build-time self-test of the
application folder: passed).

`scripts/release/smoke_test_installer.ps1` (silent, temporary install folder):

| Check | Result |
|---|---|
| Installation exit code | PASS (0) |
| `NSW.exe`, licences (`LGPL-3.0.txt` …), demo spectra installed | PASS |
| Self-test of the installed program | PASS — 11 of 11 steps, 20.4 s |
| Reinstall over the existing installation (upgrade path) | PASS |
| Uninstall exit code | PASS (0) |
| Program files removed | PASS |
| User data (`%LOCALAPPDATA%\NSW`) kept | PASS |
| Self-test output kept | PASS |

The first run failed (setup exit code 1): the script passed a log file in a folder it
had not created yet, and an unsupported `/CURRENTUSER` switch. Script fixed; the
installer was not changed.

Not covered here: the interactive wizard (licence page, folder choice, shortcuts,
launch after install), SmartScreen behaviour of the unsigned installer, a real upgrade
from an older version (none exists yet), and a machine without Python.

### 1a. Release build `v0.9.0-beta` (done 2026-11-12)

Installer `NSW-Setup-0.9.0b1-x64.exe`, 78 429 847 bytes, SHA-256
`42ba5eb95b2b4516081b798d5a527eb0d482de9c63cb2b9c4fa8af3a40d8f6db`, built from the
tree of tag `v0.9.0-beta` (`f2de73d`), including the synthetic demo spectra. Same smoke test on the development PC: all checks PASS (exit code 0). An
earlier build of the same version (SHA-256 `a8fcea2d…`) lacked the demo spectra and was
discarded.

## 2. Clean machine (owner action — not yet done)

Needed: Windows 10 or 11 x64 **without** Python, Git or uv — a second PC, a fresh local
Windows user account is *not* enough if Python is on the system PATH, or a virtual
machine (VirtualBox/VMware/Hyper-V; Windows Sandbox is not available on Windows 11 Home).

1. Copy `NSW-Setup-<version>-x64.exe`, `SHA256SUMS.txt` and
   `scripts\release\smoke_test_installer.ps1` to the machine.
2. Check the hash: `Get-FileHash .\NSW-Setup-<version>-x64.exe -Algorithm SHA256`.
3. In PowerShell:
   `powershell -ExecutionPolicy Bypass -File .\smoke_test_installer.ps1 .\NSW-Setup-<version>-x64.exe -CleanMachine`
   — expect `ALL CHECKS PASSED`.
4. Then install interactively (double-click): licence page, folder, desktop shortcut,
   *Start NSW now*; open *Demo spectra (synthetic)*; follow the walk-through in
   `docs/reports/UX_TEST_REPORT.md` §4; uninstall from *Settings → Apps*.
5. Record the results (machine, Windows version, each step) in this section.
