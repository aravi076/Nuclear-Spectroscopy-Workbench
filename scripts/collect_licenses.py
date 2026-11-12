"""Collect the licence texts of everything the Windows build distributes.

    .venv\\Scripts\\python.exe scripts\\collect_licenses.py build\\licenses

For every installed distribution the application needs at run time (the dependency
closure of ``nsw[gui]``), the licence files recorded in its metadata are copied to
``<out>/<name>-<version>/`` and listed in ``<out>/THIRD_PARTY_LICENSES.md`` with the
declared licence. Also written:

- ``LGPL-3.0.txt`` and ``GPL-3.0.txt``: PySide6 and Shiboken6 (Qt for Python) are used
  under the LGPL-3.0, which incorporates the GPL-3.0; their wheels ship only a
  reference to the commercial licence. The texts are copied verbatim from local files
  of installed packages (``autocommand`` licence for the LGPL, SciPy's bundled GPL-3.0
  for the GPL) and checked for their FSF headers.
- ``Python-LICENSE.txt``: the licence of the bundled CPython runtime.
- ``QT-LGPL-NOTICE.txt``: how the Qt libraries can be replaced (one-folder build).

Nothing is downloaded.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QT_NOTICE = """NSW uses Qt for Python (PySide6, Shiboken6) and the Qt libraries under the GNU
Lesser General Public License version 3 (LGPL-3.0, text in LGPL-3.0.txt, which
incorporates the GPL-3.0 in GPL-3.0.txt). Qt is (c) The Qt Company Ltd and other
contributors. NSW is distributed as a folder (not a single executable): the Qt and
PySide6 libraries are the separate files under _internal\\PySide6 and _internal\\shiboken6
and can be replaced by compatible builds of the same version. The source code of
Qt for Python is available from https://code.qt.io/ and https://pypi.org/project/PySide6/.
NSW does not modify Qt or PySide6.
"""


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_closure(
    root: str = "nsw", extras: tuple[str, ...] = ("gui",)
) -> list[metadata.Distribution]:
    seen: dict[str, metadata.Distribution] = {}
    todo = [(root, extras)]
    while todo:
        name, wanted = todo.pop()
        key = _norm(name)
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = dist
        for req in dist.requires or []:
            spec, _, marker = req.partition(";")
            dep = re.split(r"[\s\[<>=!~(]", spec.strip(), maxsplit=1)[0]
            extra = re.search(r"extra\s*==\s*['\"]([^'\"]+)", marker)
            if extra and extra.group(1) not in wanted:
                continue
            if "sys_platform" in marker and "win32" not in marker and "!=" not in marker:
                continue
            todo.append((dep, ()))
    return [d for k, d in sorted(seen.items()) if k != "nsw"]


def licence_files(dist: metadata.Distribution) -> list[Path]:
    out = []
    for f in dist.files or []:
        name = f.name.lower()
        if ".dist-info" in str(f) and (
            "licen" in name or "copying" in name or "notice" in name or "authors" in name
        ):
            out.append(Path(str(dist.locate_file(f))))
    return [p for p in out if p.is_file()]


def _extract(path: Path, start: str, end: str) -> str:
    text = path.read_text(encoding="utf-8", errors="strict")
    i = text.index(start)
    j = text.index(end, i) + len(end)
    return text[i:j].strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    out = parser.parse_args().out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rows = []
    for dist in runtime_closure():
        name, version = dist.metadata["Name"], dist.version
        licence = dist.metadata.get("License-Expression") or (dist.metadata.get("License") or "")
        licence = licence.splitlines()[0][:80] if licence else "see files"
        target = out / f"{_norm(name)}-{version}"
        files = licence_files(dist)
        if files:
            target.mkdir()
            for f in files:
                shutil.copy2(f, target / f.name)
        rows.append(f"| {name} | {version} | {licence} | {len(files)} file(s) |")
    site = Path(metadata.distribution("setuptools").locate_file(""))
    lgpl_src = next(site.glob("setuptools/_vendor/autocommand-*.dist-info/LICENSE"))
    lgpl = lgpl_src.read_text(encoding="utf-8")
    if not lgpl.lstrip().startswith("GNU LESSER GENERAL PUBLIC LICENSE"):
        raise SystemExit(f"not the LGPL text: {lgpl_src}")
    (out / "LGPL-3.0.txt").write_text(lgpl, encoding="utf-8")
    scipy_lic = next(
        Path(str(f.locate()))
        for f in metadata.distribution("scipy").files or []
        if f.name == "LICENSE.txt" and ".dist-info" in str(f)
    )
    gpl = _extract(scipy_lic, "GNU GENERAL PUBLIC LICENSE", "philosophy/why-not-lgpl.html>.")
    if "Version 3, 29 June 2007" not in gpl[:200]:
        raise SystemExit(f"not the GPL-3.0 text: {scipy_lic}")
    (out / "GPL-3.0.txt").write_text(gpl, encoding="utf-8")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", out / "Python-LICENSE.txt")
    (out / "QT-LGPL-NOTICE.txt").write_text(QT_NOTICE, encoding="utf-8")
    index = [
        "# Third-party licences of the NSW Windows build",
        "",
        "Generated by `scripts/collect_licenses.py` from the installed packages. NSW itself",
        "is MIT-licensed (`LICENSE`). Qt for Python: LGPL-3.0 (see `QT-LGPL-NOTICE.txt`).",
        "Python runtime: `Python-LICENSE.txt`. The PyInstaller bootloader is GPL-2.0 with an",
        "exception that permits distributing applications built with it.",
        "",
        "| Package | Version | Declared licence | Files |",
        "|---|---|---|---|",
        *rows,
    ]
    (out / "THIRD_PARTY_LICENSES.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"{len(rows)} packages; licences written to {out}")


if __name__ == "__main__":
    main()
