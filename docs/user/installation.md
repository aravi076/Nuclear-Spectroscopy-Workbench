# Installation

Python version: the desktop interface needs **Python 3.12 or later**. On 3.11 a C
extension of the interface stack (PySide6 6.12) releases Python's `None` too often and
the interpreter aborts after a while (`Fatal Python error: none_dealloc`); 3.12 is
immune. The calculation engine also runs on 3.11. The Windows installer bundles 3.12.

## One command (uv tool)

For using NSW without a source checkout. Requires `uv` (https://docs.astral.sh/uv/);
uv downloads Python 3.12 if it is not installed.

```powershell
uv tool install --python 3.12 "nsw[gui] @ git+https://github.com/aravi076/Nuclear-Spectroscopy-Workbench@v0.9.0-beta.2"
nsw                                  # start the application
nsw --self-test $env:TEMP\nsw-check  # optional: end-to-end check on synthetic data
uv tool uninstall nsw                # remove
```

- Install from the GitHub URL only. NSW is not published on PyPI, and a package named
  `nsw` there may belong to someone else.
- The tag (`@v0.9.0-beta.2`) pins the NSW version. The dependencies are resolved when you
  install, not taken from `uv.lock`, so they can be newer than the tested versions; the
  self-test shows whether everything works. For exactly the tested versions use the
  Windows installer or the development setup below.
- Tested 2026-11-13 on Windows 11 with uv 0.11.7: installed in about 30 s, self-test 11
  of 11 steps passed, uninstall clean.

## Development setup

Windows 11, PowerShell. Requires `uv` (https://docs.astral.sh/uv/).

```powershell
cd Nuclear-Spectroscopy-Workbench
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[gui,dev]"
Copy-Item config\nsw.example.yaml config\local.yaml   # edit the paths
.venv\Scripts\python.exe -m pytest
```

Estimated disk use: about 1 GB for `.venv` with all extras.

## Windows executable (PyInstaller)

```powershell
uv pip install --python .venv\Scripts\python.exe "pyinstaller>=6.10"
.venv\Scripts\python.exe -m PyInstaller packaging\windows\NSW.spec --noconfirm --distpath build\dist --workpath build\work
```

Output: `build\dist\NSW\NSW.exe` with its `_internal` folder (about 270 MB; copy the
whole `NSW` folder). No installer is produced and no laboratory data or configuration is
bundled. Check a build with its end-to-end self-test, which uses only generated synthetic
spectra and writes only into the given folder:

```powershell
build\dist\NSW\NSW.exe --self-test C:\Temp\nsw-selftest
type C:\Temp\nsw-selftest\selftest.log
```

All 11 steps must report `ok` (load, peak search and fits, efficiency, activity, nuclide
candidates, project save and reopen with reproduced fits, PDF report, SVG figure).

Tested 2026-11-12: build copied outside the project folder and started with a stripped
environment (no Python and no virtual environment on PATH): 11 of 11 steps passed. Not
yet tested on a separate, freshly installed Windows machine. Before distributing an
executable, add the LGPL-3.0 and GPL-3.0 texts (`THIRD_PARTY_NOTICES.md`).
