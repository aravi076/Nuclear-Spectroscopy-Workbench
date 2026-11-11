# NSW Architecture

Status: proposed in Phase 0; implemented through Phase 4. Where the text below differs
from the code, the "As built" section at the end and TECHNICAL_DECISIONS.md take precedence.

## Layers and dependency rule

```
ui  ──►  services  ──►  analysis / simulation / reporting
                   ──►  detectors
                   ──►  io, database
                              all ──►  core
```

Imports only point downwards/rightwards. `core`, `analysis`, `detectors`, `io` and
`simulation` never import Qt, never touch SQLite and never read configuration files;
they take and return plain validated objects. This is enforced by a test that inspects
imports (Phase 1).

| Spec layer | Package | Responsibility |
|---|---|---|
| A Presentation | `nsw.ui` | PySide6 windows, PyQtGraph plots, tables, dialogs, QThread workers. No physics. |
| B Application services | `nsw.services` | Use-case orchestration: import a folder, run a workflow, manage a project, build a report. Only layer the UI calls. |
| C Scientific engine | `nsw.analysis` | background, peak search, peak fit, energy/resolution/efficiency calibration, activity, uncertainty, nuclide ID. Pure functions on `core` objects. |
| D Detector management | `nsw.detectors` | Detector profiles (HPGe, NaI(Tl)), resolution/efficiency model definitions, geometry, calibration history. |
| E Data management | `nsw.core`, `nsw.io`, `nsw.database` | Data model; file adapters; SQLite project store and reference-data store. |
| – | `nsw.simulation` | Synthetic spectrum generator with stored ground truth. |
| – | `nsw.reporting` | Matplotlib figures, PDF/CSV/PNG/SVG output. |

## Core data model (`nsw.core`)

- `Spectrum` — frozen object: `counts` (read-only NumPy array, int64 for measured data),
  `kind` (`RAW | BACKGROUND | NET | PROCESSED | SYNTHETIC`), `live_time_s`,
  `real_time_s`, `start_time` (timezone-aware or explicitly "naive local"),
  `detector_id`, `source: SourceFile | None` (path, size, SHA-256, adapter name),
  `metadata` (vendor key/values, untouched), `embedded_calibration` (untrusted),
  `calibration_id` (the NSW calibration actually applied, if any), `provenance`
  (ordered list of processing steps with parameters).
- Net/processed spectra carry `values` (float64) **and** `variance` (float64); raw
  spectra derive variance from Poisson statistics on demand. Negative net values are kept.
- `channels` are 1-based to match the instrument and its exports; array index =
  channel − 1. This is stated once here and tested.
- `EnergyCalibration`, `ResolutionCalibration`, `EfficiencyCalibration` — coefficients,
  covariance, input points, method, date, detector id, validity range, origin
  (`fitted | imported-vendor | assumed`).
- `Peak` (candidate vs confirmed), `PeakFit` (parameters, covariance, χ²/ndf, flags).
- `Quantity`-style value/uncertainty pairs with explicit unit strings; statistical and
  systematic components kept separately.
- Validation with Pydantic v2 at the boundaries (file import, configuration, database
  rows); NumPy arrays are checked for dimension, dtype, finiteness and length.

## File import (`nsw.io`)

`SpectrumReader` protocol: `sniff(path, head_bytes) -> confidence` and
`read(path) -> ImportResult` (spectrum + warnings). A registry picks the reader by
content, not extension. Adapters planned from the Phase 0 audit:

1. `LynxAvaAdapter` — Canberra/Mirion Lynx XML (`.ava`).
2. `LynxCsvAdapter` — Lynx CSV export.
3. `CanberraCnfAdapter` — CAM binary (`.cnf`), reverse-engineered, validated against CSV.
4. `GenericCsvAdapter` / `GenericTxtAdapter` — one- or two-column text.
5. `SpeAdapter` (IAEA/Ortec ASCII SPE), `ChnAdapter` (Ortec integer CHN), `McaAdapter`
   (Amptek ASCII MCA) — implemented from published format descriptions and tested on
   synthetic fixtures only until real files are available; labelled as such.

New formats are added by registering a reader (entry in a registry module), not by
editing existing code. Import never writes to the source directory.

## Persistence (`nsw.database`)

One SQLite file per NSW project in the user workspace. Tables: `project`, `detector`,
`spectrum` (metadata + file hash + path, counts stored as a compressed blob so a project
can be reopened if the source moves), `calibration` (append-only history),
`analysis_run` (settings JSON, software version, input hashes), `peak`, `activity_result`,
`reference_dataset` (source, version, retrieval date), `audit_log`. Results are
append-only: re-running creates a new `analysis_run`; nothing is overwritten. Schema
version table plus timestamped backup copy before any migration.

## Nuclear reference data

Stored in `data/reference/` with a provenance record (source, dataset/version, URL,
retrieval date, licence). Candidate source: IAEA Live Chart of Nuclides data API
(ENSDF-derived decay radiation), with DDEP/LNHB recommended data for calibration
nuclides. Selection and licensing are checked in Phase 9 (energies for calibration are
needed from Phase 6, so a minimal vetted calibration-line table is introduced there).
No reference value is typed in from memory.

## UI (`nsw.ui`)

`QMainWindow` with a navigation list of the 14 pages in the specification and a stacked
central area. Long operations run in `QThreadPool` workers that call `services`; progress
and errors come back through signals. Plots: PyQtGraph for interaction, Matplotlib for
exported figures.

## Cross-cutting

- Configuration: `config/local.yaml` (git-ignored) → Pydantic settings.
- Logging: standard `logging`, rotating file in the workspace.
- Reproducibility: every result row references software version, input file hashes,
  calibration ids and the full settings used.

## As built (Phases 1–9)

| Package | Modules |
|---|---|
| `nsw.core` | `errors`, `units`, `calibration`, `spectrum`, `nuclear_data` |
| `nsw.detectors` | `profiles` (HPGeProfile, NaIProfile) |
| `nsw.io` | `base`, `lynx_csv`, `lynx_ava`, `canberra_cnf`, `nsw_synthetic`, `registry`, `identity`, `integrity`, `reference_data` |
| `nsw.analysis` | `background`, `peaks` (search), `fitting` (Gaussian fits, multiplet evidence), `energy_calibration` (regression, order test, stability), `resolution` (FWHM model), `decay`, `efficiency`, `activity`, `identification` |
| `nsw.simulation` | `generator`, `presets`, `sources` (fictitious sources and efficiency), `reference` (fictitious library) |
| `nsw.services` | `dataset`, `describe`, `export`, `background`, `integrity`, `peaks`, `calibration`, `resolution`, `efficiency` (gate for experimental data), `identification` (calibration gate), `validation` (Monte Carlo studies) |
| `nsw.ui` | `app`, `main_window`, `state`, `workers`, `plot`, `fit_plot`, `tables`, `dashboard`, `explorer`, `background_page`, `peak_search_page`, `peak_fit_page`, `calibration_page`, `resolution_page` |
| `nsw.config` | settings (outside the layers) |
| `nsw.database`, `nsw.reporting` | empty; not started |

Peak results are plain result objects (`PeakCandidate`, `FitResult`) defined in
`nsw.analysis`, not in `core` as proposed in Phase 0; they move to `core` only if the
database layer needs them (Phase 11). Pages start long calculations through a runner
supplied by the main window (`QThreadPool` workers); results come back by signal to
bound methods of the page, never to lambdas.

A code knowledge graph (Graphify, AST only) lives in `graphify-out/` (git-ignored).
Refresh with `graphify update .` after structural changes.

Differences from the Phase 0 proposal: `kind`/`origin` are separate (TD-009); only
verified formats have adapters (TD-011); settings moved to `nsw.config` (TD-017);
SQLite project storage, reference data and the remaining GUI pages are not built yet.
