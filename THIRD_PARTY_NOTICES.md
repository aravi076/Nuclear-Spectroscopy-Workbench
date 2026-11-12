# Third-Party Notices

NSW contains no third-party source code: every file under `src/`, `tests/` and
`scripts/` was written for this project. It **depends on** the packages below, which
are installed separately from PyPI and are not redistributed in this repository. Each
remains under its own licence and copyright; the NSW licence does not apply to them.

Licences as declared in the metadata of the versions installed on 2026-11-11.

## Runtime dependencies

| Package | Version tested | Licence | Used for |
|---|---|---|---|
| NumPy | 2.5.4 | BSD-3-Clause (with bundled 0BSD, MIT, Zlib, CC0-1.0 components) | arrays, linear algebra |
| SciPy | 1.18.1 | BSD-3-Clause | special functions, distributions |
| Pydantic (and pydantic-core, annotated-types, typing-inspection) | 2.14.3 | MIT | validated data records |
| typing-extensions | 4.16.0 | PSF-2.0 | dependency of Pydantic |
| PyYAML | 6.0.3 | MIT | reading the settings file |
| pandas (and python-dateutil, tzdata) | 3.1.1 | BSD-3-Clause (python-dateutil BSD-3-Clause, tzdata Apache-2.0) | declared dependency; not imported by NSW yet |

## Desktop interface (optional extra `gui`)

| Package | Version tested | Licence | Used for |
|---|---|---|---|
| PySide6-Essentials, shiboken6 (Qt for Python) | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | windows, widgets, threads |
| PyQtGraph | 0.14.0 | MIT | interactive plots |
| Matplotlib (and contourpy, cycler, fonttools, kiwisolver, packaging, pillow, pyparsing) | 3.11.2 | Matplotlib licence (PSF-based); dependencies BSD-3-Clause, MIT, MIT-CMU, Apache-2.0 OR BSD-2-Clause | declared dependency for report figures; not imported by NSW yet |

Qt for Python is used under the LGPL-3.0 option: NSW imports it as a separately
installed library and does not modify or statically link it. The Windows build
(`packaging/windows/NSW.spec`, one folder) keeps the Qt libraries as separate, replaceable
DLLs. The PySide6 wheels used for the build contain no LGPL text, so **before an
executable is distributed** the texts of the LGPL-3.0 and GPL-3.0
(https://www.gnu.org/licenses/lgpl-3.0.txt, https://www.gnu.org/licenses/gpl-3.0.txt)
must be added to it, together with this file. No executable has been distributed.

## Development tools (optional extra `dev`; not needed to run NSW)

| Package | Version tested | Licence |
|---|---|---|
| pytest | 9.2.0 | MIT |
| pytest-cov | 7.1.0 | MIT |
| Ruff | 0.17.4 | MIT |
| mypy | 2.4.1 | MIT |

## File formats

The readers for Canberra/Mirion Lynx CSV, Lynx XML (`.ava`) and CAM (`.cnf`) files
were written from inspection of files in those formats. The CAM layout is not
publicly specified; the reader uses layout knowledge from community
reverse-engineering and contains no vendor code. "Canberra", "Mirion", "Lynx" and
"Genie" are trademarks of their owners; NSW is not affiliated with them.

## Methods

Algorithms are implemented from the published literature and cited where used, for
example L. A. Currie, Anal. Chem. 40 (1968) 586 for the detection limit. No code was
taken from those publications.
