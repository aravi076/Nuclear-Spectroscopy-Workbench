# Installation

Development setup (Windows 11, PowerShell). Requires `uv` (https://docs.astral.sh/uv/).

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
