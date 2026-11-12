# GitHub Publication Audit

Date: 2026-11-11. Status: **prepared, not published.** No GitHub repository, remote,
push, tag or release exists. Publication waits for the owner (section 11).

## 1. Public-candidate directory

A folder named `Nuclear-Spectroscopy-Workbench-public`, next to the private development
repository on the author's machine. Proposed repository name:
`Nuclear-Spectroscopy-Workbench`. The private repository is the development archive and
was not modified by this work, apart from new commits that record this preparation.

## 2. Files included (139)

| Group | Files |
|---|---|
| Source code `src/nsw/` | 65 (`core`, `io`, `detectors`, `analysis`, `simulation`, `services`, `ui`; empty `database`, `reporting`) |
| Tests `tests/` | 32 Python files: 14 unit, 6 integration, 8 validation modules, fixtures and configuration; 4 placeholders |
| Scripts | `generate_synthetic.py`, `make_screenshots.py`, `validate_peak_fitting.py`, `verify_dataset_integrity.py` |
| Root documents | `README.md`, `CHANGELOG.md`, `CITATION.cff`, `CONTRIBUTING.md`, `SECURITY.md`, `DATA_LICENSES.md`, `THIRD_PARTY_NOTICES.md`, `LICENSE`, `TECHNICAL_DECISIONS.md`, `VALIDATION_REPORT.md` |
| `docs/` | architecture, installation, user manual, file formats, scientific methodology, nuclear-data policy, survey of public datasets, roadmap, release notes, this audit |
| Screenshots `docs/images/` | 6 PNG, generated from synthetic spectra, each inspected |
| Configuration | `pyproject.toml`, `.gitignore`, `config/nsw.example.yaml` (placeholder paths) |
| Placeholders | empty `data/reference/`, `data/synthetic/`, `reports/` |

Existing document names were kept (`docs/user/installation.md`, `docs/design/architecture.md`,
`docs/science/scientific_methodology.md`, `VALIDATION_REPORT.md`) instead of creating
duplicates under other names.

## 3. Files excluded

| Excluded | Reason |
|---|---|
| The previous Git history | contained a personal folder path, an instrument serial number and a personal e-mail address |
| Laboratory measurements (instrument CSV, CNF, XML and PDF files) | private; were never in any repository |
| Private configuration, hash manifest, workspace (`config/local.yaml`, `local/`) | personal paths and data about the private measurements |
| Virtual environment, caches, coverage data, generated synthetic spectra, code graph | generated locally; git-ignored |
| Dataset inventory, audit scripts for the private dataset, the test module and four tests that read it, the validation-report section on those measurements | they describe the private measurements (owner decision) |
| Project status, development log, development reports, master specification, assistant instructions | internal working documents that quote the private measurements throughout |
| Dashboard screenshot | the page displays the folder path of the loaded data, which showed a user-profile path |
| Databases, logs, build output, tokens, credentials | none exist in the project |

No scientific record was altered to hide an identifier: records containing private
identifiers were left out as a whole.

## 4. Sensitive-data findings and remediation

| Finding | Where it was | Remediation |
|---|---|---|
| Personal Windows folder path of the dataset | a document, in every earlier commit | history excluded; document excluded |
| Instrument serial number | a document and two tests, in earlier commits | history excluded; document and tests excluded or changed |
| Personal e-mail address | author field of every earlier commit | history excluded; new commit uses a placeholder address |
| Project folder path | installation guide, user manual | replaced by a relative path |
| User-profile path | dashboard screenshot | screenshot removed; the script no longer produces it |
| Measured values, dates, file names, detector settings of the private dataset | several documents, tests, one docstring, one input hint | removed |

Searches repeated on the staged files and on the commit, case-insensitive: the
personal name of the former path, the serial number, e-mail addresses, the account
name, drive-letter and user-profile paths, credential patterns, institutional terms,
and the measured values and file names of the private dataset. Result: none. Remaining
intended hits: the author's name (README, `CITATION.cff`, commit author), placeholder
paths in the example configuration, public URLs, a fabricated path `X:/data/...` in a
test helper, and fabricated test files that imitate instrument naming with invented
counts. Example time stamps in the format documentation carry a GMT+05:30 offset.

## 5. Git history

One initial commit on branch `main`, created in a freshly initialised repository. No
earlier commit, branch or tag was copied. Later commits are made in this repository
from reviewed changes. The commit identifier is recorded in the
private repository's copy of this report, since a file cannot contain the identifier
of the commit that contains it. Author field: the owner's name with the GitHub no-reply address shown in the owner's
GitHub e-mail settings; no personal address.

## 6. Licence status and ownership

**MIT License (SPDX: MIT).** `LICENSE` holds the unmodified MIT text with
`Copyright (c) 2026 Aravind Loshana A`; `pyproject.toml` declares `license = "MIT"`.

Basis: on 2026-11-11 the owner confirmed the copyright holder's name and stated that
no university restrictions apply to licensing the code. This is the owner's
declaration; it was not verified against any institutional policy or agreement.

Scope: original NSW code and documentation only. Third-party packages, laboratory
measurements and nuclear data are outside it (`THIRD_PARTY_NOTICES.md`,
`DATA_LICENSES.md`).

## 7. Dependency licence review

No third-party source code is contained in the repository. Dependencies are installed
from PyPI and listed with versions and licences in `THIRD_PARTY_NOTICES.md` (read from
the installed package metadata on 2026-11-11): BSD-3-Clause (NumPy, SciPy, pandas),
MIT (Pydantic, PyYAML, PyQtGraph, pytest, Ruff, mypy), LGPL-3.0 option for Qt for
Python (PySide6), Matplotlib licence. All are compatible with an MIT licence for NSW's
own code. PySide6 imposes obligations on a future packaged executable, not on the
source repository. pandas and Matplotlib are declared dependencies that the code does
not import yet; the README says so.

## 8. Nuclear-data redistribution

No nuclear decay data are included. DDEP (LNE-LNHB) and ENSDF (NNDC) publish citation
rules but no redistribution licence on their sites (checked 2026-11-11), so neither is
bundled or downloaded; users import their own files (`docs/science/nuclear_data.md`,
`DATA_LICENSES.md`). Test nuclides are fictitious and labelled.

## 9. Test results from the cleaned copy (2026-11-11)

Run inside the public-candidate directory with its own fresh environment
(`uv venv`, `uv pip install -e ".[gui,dev]"`, as in the README).

| Check | Result |
|---|---|
| `pytest` | 405 passed, 0 failed, 0 skipped (217 unit, 35 integration, 153 validation); at first publication 390 |
| Coverage of non-GUI code | 97 % |
| `ruff check`, `ruff format --check` | clean |
| `mypy --strict` on `src/nsw` | clean (64 source files) |
| `scripts/generate_synthetic.py` | wrote 12 synthetic spectra |
| Application start and seven pages (`scripts/make_screenshots.py`) | ran; package imported from this directory |
| README library example | ran |
| `scripts/validate_peak_fitting.py` (Monte Carlo tables of the validation report) | ran to completion, exit code 0 |
| Private laboratory dataset | unchanged: 257 files match the SHA-256 manifest (verified from the private repository, read-only) |

Experimental validation: none in this repository.

## 10. Checklist

| Item | State |
|---|---|
| Sensitive-data audit | passed |
| Git history audit | passed (single new commit) |
| Original experimental data excluded | yes |
| Ownership and licence | confirmed by the owner (declaration, 2026-11-11); MIT applied |
| Dependency licences reviewed | yes |
| Nuclear-data redistribution checked | yes: nothing bundled |
| README and documentation | complete |
| Tests rerun in the cleaned copy | yes |
| Initial commit created | yes |
| Owner's approval to publish | **open** |

## 11. Remaining blockers and notes

1. Explicit approval of public visibility by the owner.
2. Recommended before pushing: in GitHub e-mail settings switch on "Keep my email
   addresses private" and "Block command line pushes that expose my email".
3. The independent review of the efficiency, activity and identification engines was
   completed after first publication; its corrections are the second commit and its
   open findings are in `docs/reports/REVIEW_PHASE8_9.md`.
4. Interface tests are programmatic; systematic manual testing is outstanding.

## 12. Publishing (after approval)

In the public-candidate directory:

```powershell
# 1. licence and author address are already in place; repeat the checks
.venv\Scripts\python.exe -m pytest
git log --format="%an <%ae>"          # must show the no-reply address only

# 2. create an EMPTY public repository on github.com named
#    Nuclear-Spectroscopy-Workbench (no README, licence or .gitignore), then
git remote add origin https://github.com/<username>/Nuclear-Spectroscopy-Workbench.git
git push -u origin main

# 3. optional pre-release
git tag -a v0.1.0-alpha -m "NSW v0.1.0-alpha"
git push origin v0.1.0-alpha
#    on GitHub: Releases > Draft a new release > tag v0.1.0-alpha > paste
#    docs/release/RELEASE_NOTES_v0.1.0-alpha.md > tick "This is a pre-release"
```

After publishing, enable "Private vulnerability reporting" in the repository's
security settings (`SECURITY.md` refers to it).

## 13. Later updates

The private repository stays the development workspace. Changes are transferred to the
public repository selectively, reviewed for private data, tested there, and committed
with descriptive messages. Nothing is copied wholesale.
