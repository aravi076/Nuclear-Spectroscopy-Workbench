# UX Redesign Plan — Interactive, Mouse-First Spectroscopy

Status: **Approved by the owner on 2026-11-12**, including the decisions listed at the end.
10 and 11 of the Master Development Directive on top of the existing PySide6 +
PyQtGraph application. Nothing here changes a calculation; every number still comes
from `nsw.analysis` through `nsw.services`.

## 1. Starting point

| Today | Limitation |
|---|---|
| `SpectrumPlot` (`ui/plot.py`): wheel zoom, drag pan, reset, hover read-out of channel and counts | no rectangle zoom, no selection of regions or peaks, no context menu, no shortcuts except Ctrl+O / Ctrl+S |
| Peak Analysis page: search with typed settings; Peak Fitting page: fit regions typed as first/last channel | the analyst translates what they see into numbers |
| Energy Calibration: reference energies typed per peak | no suggestion from reference data |
| 11 independent pages in a left list (`PAGE_*` 0–10) | no guided order; results pass between pages only through `AppState` |
| `AppState` signals: `changed`, `peaks_changed`, `fits_changed`, `calibrations_changed`, `efficiency_changed` | no notion of a region of interest or of analyst decisions |

## 2. Principles

1. **The spectrum is the workspace.** Selection, fitting and review happen on the plot;
   tables mirror the plot and are editable as a second route.
2. **Automation proposes, the analyst decides.** Candidates, multiplet groupings and
   line assignments are suggestions until accepted; every acceptance or rejection is
   recorded (see `docs/design/SCIENTIFIC_PROVENANCE_DESIGN.md`).
3. **Selections never alter data.** An ROI is a pair of channel numbers plus intent.
4. **Gates stay visible.** Channel axis while the calibration is unverified (TD-016);
   reference-line overlays only with a VERIFIED calibration; experimental efficiency
   and activity refused with the list of missing inputs.
5. **One code path** for beginner and advanced modes (R10).
6. **Responsive with 16 384 channels**: draw with `setClipToView(True)` and
   `setDownsampling(auto=True, mode="peak")`; rate-limit drag updates (≤ 30 Hz);
   heavy work in `QThreadPool` workers with bound-method callbacks (TD-036).

## 3. Interaction model of the plot

Modes (toolbar toggle group plus keys); the active mode is shown in the status bar.

| Mode | Key | Mouse | Effect |
|---|---|---|---|
| Navigate (default) | `Esc` | drag = pan, wheel = zoom about cursor, double-click = reset | as today |
| Rectangle zoom | `Z` | drag a box | zoom to the box (ViewBox `RectMode`), returns to Navigate |
| ROI | `R` | drag along x | creates an ROI (`pg.LinearRegionItem`); edges draggable; body draggable |
| Select | `S` | click a peak marker; Ctrl-click adds; drag selects markers in a span | selection drives the side panel |

Always available: `Home` reset view; `Ctrl+Z` / `Ctrl+Y` undo / redo; `Del` delete the
selected ROI or exclude the selected candidates; `F` fit the selected ROI; `Shift+F` fit
all accepted candidates; `L` toggle log/linear y; `+`/`-` sensitivity of the search;
right-click context menu on empty plot / ROI / peak marker with the same actions.
Hover tooltip: channel, counts (± √counts for raw data), and, only with a verified
calibration, energy with its uncertainty.

Undo/redo uses a `QUndoStack` holding small command objects (add, move, resize, delete
ROI; include/exclude candidate; add manual candidate; accept/reject assignment). The
stack is per spectrum and is cleared only when the spectrum is closed.

## 4. Region of interest

### 4.1 Model (services layer, Qt-free)

```text
nsw.services.roi
  RoiKind      = SINGLE | MULTIPLET | CUSTOM
  Roi          (roi_id, data_sha256, first_channel, last_channel, kind, label,
                peak_channels: tuple[float, ...])          # immutable
  validate(roi, spectrum)            -> list[str]          # inside the spectrum, ≥ 3 channels, …
  candidates_in(roi, search_result)  -> list[PeakCandidate]
  continuum_estimate(roi, spectrum)  -> Quantity           # from the edge channels (as fits do)
  propose_rois(search_result, resolution) -> list[Roi]     # one ROI per candidate group
  fit_roi(roi, spectrum, settings, model) -> FitResult     # wraps analysis.fitting
```

`propose_rois` groups candidates closer than a configurable multiple of the expected
FWHM (default 1.5, from the resolution model if one exists, else from the search) into
MULTIPLET ROIs and extends each ROI by a configurable number of FWHM (default 3) on each
side for the continuum. It never merges silently: the panel says why candidates were
grouped.

### 4.2 Panel (right side)

For the selected ROI: start/end channel (editable spin boxes, synchronised with the
plot edges), kind, number of candidates and their positions, continuum estimate with
uncertainty, fit model, fit status, area, centroid, FWHM with uncertainties, χ²/ndf,
p-value, runs-test z, warnings. Buttons: *Fit*, *Refit after edit*, *Compare models*,
*Accept fit*, *Discard*.

### 4.3 Persistence

ROI events are stored append-only (schema v2, `docs/design/SCIENTIFIC_PROVENANCE_DESIGN.md`
§4). Reopening a project restores the latest ROI set and recomputes accepted fits, as
fits are reproduced today.

## 5. Automatic peak detection on load

When a spectrum is opened (or on demand for a region):

1. Width range from the detector profile (HPGe vs NaI(Tl)) or, when present, the
   fitted resolution model (R12); not from fixed numbers.
2. Existing scale-space search (`analysis.peaks.find_peak_candidates`) with the
   profile's default significance threshold.
3. Candidates drawn as markers coloured by quality class (strong / weak / doubtful) with
   a legend; doubtful = below threshold+1σ, width inconsistent with the resolution
   model, or shape flags.
4. Candidates that look like Compton edges or continuum structure (width far above the
   resolution model, strong asymmetry) are labelled *not photopeak-like* and are not
   proposed for fitting.

Controls: *Search spectrum*, *Search selected ROI*, sensitivity slider (threshold in σ),
*Add candidate here* (context menu at the cursor), *Exclude*, *Restore*. Detected
candidates are never called transitions or nuclides.

Detection runs in a worker. Its duration on a 16 384-channel spectrum is measured
before it is switched on by default (target: under one second).

## 6. Fitting workflow

- *Fit selected peak* → a SINGLE ROI proposed around it; *Fit ROI*; *Fit all accepted*
  → one fit per proposed ROI, in a worker with a progress bar and cancel.
- After a fit the plot overlays: data, total model, continuum, each component; a
  residual strip below the spectrum (normalised residuals, ±2 lines) shares the x axis.
- Controlled adjustment: edges by dragging, start positions by dragging the component
  marker, width fixed/free, shared width, continuum shape (none/constant/linear),
  optional step (exists), optional tail (only after `docs/design/HPGE_PEAK_TAIL_DESIGN.md`).
- *Compare models*: fits the alternatives side by side (table of deviance, Δdeviance,
  AIC, areas with uncertainties). The simpler model stays selected unless the analyst
  chooses otherwise; the comparison is stored.
- Poor fits are flagged with their likely cause (p-value, runs test, parameter at a
  bound, unresolved shoulder from `multiplet_evidence`, too few channels).

## 7. Nuclear Data Explorer (new page) and line overlay

- Lists the libraries loaded in the session: fictitious library always; DDEP/ENSDF files
  the user imports (`load_reference_file`). Nothing real is bundled until
  redistribution terms are confirmed (`DATA_LICENSES.md`).
- Per nuclide: half-life, decay modes, lines (energy ± u, emission probability ± u or
  relative intensity, clearly labelled), and all provenance fields (evaluation,
  dataset identifier, retrieval date, citation, licence, transformations).
  Conflicts between evaluations shown side by side, never merged.
- Overlay on the plot: select nuclides, toggle lines, filter by energy and minimum
  emission probability; drawn only on a spectrum with a VERIFIED calibration; otherwise
  disabled with the reason.
- *Match selected peak*: candidate lines inside the window
  |ΔE| ≤ k·√(u_peak² + u_line² + u_cal²), k = 3 (`docs/science/nuclear_data.md`), with the
  other expected lines of each nuclide, missing strong lines and interferences — from
  `analysis.identification`. Accept / reject per assignment (recorded). Rankings are
  labelled as rankings (R11).

## 8. Assisted energy calibration

On the Energy Calibration page:

1. Select fitted peaks (plot or table).
2. *Suggest assignments*: for a spectrum with an approximate gain (provisional
   calibration or a user-entered gain and offset with stated uncertainty) and a loaded
   library, candidate lines per peak ranked by normalised distance, with provenance.
3. Accept / reject per row; duplicates (one line to two peaks, two lines to one peak)
   blocked with a message.
4. *Fit*: existing effective-variance fit, linear by default, quadratic offered with
   the existing order test; diagnostics already present (residuals, coverage,
   extrapolation, conditioning) plus outlier flags (|normalised residual| > 3) and
   "too few points / poor coverage" warnings before fitting.
5. Result status stays PROVISIONAL for experimental spectra whose source identities are
   not verified by the owner; the gate does not change.

## 9. Guided workflow and modes

- A *Workflow* panel (collapsible, left of the plot under the navigation) lists the 12
  stages of Part 10 with a status per stage (not started / needs review / done /
  blocked: reason). Clicking a stage opens the relevant page with the current spectrum
  selected. Expert pages remain reachable directly.
- **Beginner mode**: workflow panel open, explanations visible, advanced controls
  hidden, suggested next step highlighted. **Advanced mode**: panel collapsed, all
  controls visible. The mode is a view setting stored in the settings file; state and
  calculations are shared (R10).
- Valid results flow automatically between stages through `AppState`; anything
  ambiguous (multiplet grouping, assignments, calibration order) waits for review.

## 10. Layout

Left: navigation + workflow panel (collapsible). Centre: plot with residual strip
(splitter). Right: context panel for the current selection (dock, closable). Bottom:
status and warnings area (collapsible). All splitters remember their sizes per user.
Minimum supported window 1366 × 768: the right panel can be undocked and the left panel
collapses to icons; at 1920 × 1080 all panels are open. Light theme first; a dark theme
with the same palette tokens later if time allows. Colour-blind-safe series colours
(the palette already used in reports).

## 11. Testing

- Unit tests for `services.roi` (validation, grouping, continuum estimate) with
  synthetic spectra and exact expectations.
- pytest-qt tests with real mouse events (`qtbot.mousePress/Move/Release` on the plot
  viewport) for: ROI creation by drag, edge drag, undo/redo, context-menu actions,
  shortcuts, fit from ROI; offscreen.
- Performance test: 16 384-channel spectrum, ROI drag → update under 50 ms median.
- **Manual walk-through** with a written script at 1366 × 768 and 1920 × 1080 before any
  release, recorded in `docs/reports/UX_TEST_REPORT.md`. Programmatic tests are not presented as
  GUI validation.

## 12. Implementation order and size

| Step | Content | Size |
|---|---|---|
| U1 | `services.roi` + tests; schema v2 with `roi_event`/`decision_event` | medium |
| U2 | plot modes, ROI item, selection, context menu, shortcuts, undo stack | medium-large |
| U3 | ROI panel, fit from ROI, residual strip, model comparison | medium |
| U4 | automatic search on load, quality classes, manual add/exclude | medium |
| U5 | Nuclear Data Explorer page, overlay, match selected peak | medium |
| U6 | assisted calibration | medium |
| U7 | workflow panel, beginner/advanced modes, layout docks | medium |
| U8 | manual walk-through, `UX_TEST_REPORT.md` | small |

Each step is a feature branch with tests, merged when green.

## 13. Decisions requested from the owner

1. Approve this plan (or name changes).
2. Default ROI grouping distance 1.5 × FWHM and margin 3 × FWHM — acceptable as
   defaults (both editable)?
3. Is a user-entered approximate gain (with uncertainty) acceptable as the basis for
   *suggested* assignments on experimental spectra, given that the result stays
   PROVISIONAL?
