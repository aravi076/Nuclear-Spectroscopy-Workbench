# Installation

Development setup (Windows 11, PowerShell). Requires `uv` (https://docs.astral.sh/uv/).

```powershell
cd Nuclear-Spectroscopy-Workbench
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[gui,dev]"
Copy-Item config\nsw.example.yaml config\local.yaml   # edit the paths
.venv\Scripts\python.exe -m pytest
```

Estimated disk use: about 1 GB for `.venv` with all extras; 0.5–1 GB more for PyInstaller
build output (Phase 12). End-user installer instructions will be added in Phase 12.
