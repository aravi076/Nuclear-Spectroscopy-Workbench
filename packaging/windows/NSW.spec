# PyInstaller specification for the NSW Windows build (one folder, windowed).
#
#   scripts\release\build_windows.ps1   (collects licences, builds, makes the installer)
#   .venv\Scripts\python.exe -m PyInstaller packaging\windows\NSW.spec --noconfirm
#
# Output: dist\NSW\NSW.exe. Development tools (pytest, mypy, ruff) and unused Qt/
# scientific modules are excluded; no laboratory data or configuration is bundled.
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
LICENCES = ROOT / "build" / "licenses"  # written by scripts/release/collect_licenses.py
if not LICENCES.is_dir():
    raise SystemExit("run scripts/release/collect_licenses.py build/licenses first")

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "nsw_launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(ROOT / name), ".") for name in ("LICENSE", "THIRD_PARTY_NOTICES.md",
           "DATA_LICENSES.md") if (ROOT / name).exists()]
          + [(str(LICENCES), "licenses")],
    hiddenimports=["matplotlib.backends.backend_pdf", "matplotlib.backends.backend_svg",
                   "matplotlib.backends.backend_agg"],
    excludes=["pytest", "_pytest", "mypy", "ruff", "IPython", "tkinter", "matplotlib.tests",
              "numpy.tests", "scipy.tests", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
              "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NSW",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="NSW")
