# Technical Decisions

Each entry: decision, reason, alternatives. Append; do not rewrite history.

## TD-001 Virtual environment with uv-managed CPython 3.12 (2026-11-10)
The only Python on PATH is the Anaconda base (3.13.9) with many global packages. Virtual
environments derived from a conda base are fragile on Windows and PyInstaller bundles
built from them tend to pick up conda DLLs. `uv venv --python 3.12` gives an isolated
standalone interpreter. 3.12 is chosen as the most conservative version supported by all
planned dependencies; code targets ≥ 3.11.

## TD-002 Vendor-embedded calibrations are untrusted metadata (2026-11-10)
The energy calibration stored in the files of the development dataset is wrong. NSW stores embedded calibrations with origin `imported-vendor`, displays them, and
requires an explicit user action to apply one.

## TD-003 Channels are 1-based (2026-11-10)
The Lynx exports number channels 1…8192 and compute energy as `a + b·channel` on that
basis. NSW uses the same convention everywhere (array index = channel − 1) so calibration
coefficients are comparable with the instrument's.

## TD-004 PySide6-Essentials instead of the full PySide6 meta-package (2026-11-10)
Only QtCore/QtGui/QtWidgets (+ OpenGL/SVG, included in Essentials) are needed. Essentials
is the same binding, without WebEngine/3D/Multimedia add-ons, saving several hundred MB
of the 15 GB allocation and shrinking the PyInstaller bundle. Revisit if an add-on
module is needed.

## TD-005 PDF reports via Matplotlib `PdfPages` (2026-11-10)
Avoids an extra dependency (ReportLab etc.). Tables and text are laid out as Matplotlib
pages. Revisit in Phase 12 if layout needs exceed this.

## TD-006 File pairing by content, not by name (2026-11-10)
The dataset contains same-named files in different formats holding different
acquisitions. Acquisitions are identified by (start time, live time, counts hash).
Format priority for the same acquisition: AVA > CSV > CNF (metadata richness and timing
precision; CNF real time is rounded to whole seconds).

## TD-007 GUI dependencies are an optional extra (2026-11-10)
`pip install nsw` installs the scientific engine only; `nsw[gui]` adds Qt, PyQtGraph and
Matplotlib. Guarantees the engine runs headless and keeps CI/test environments light.

## TD-008 Laboratory identifiers in tracked documents (2026-11-10)
Descriptions of private measurements, hash manifests and personal paths are not part
of the public repository; locally they stay in git-ignored `local/` and
`config/local.yaml`.

## TD-009 Spectrum kind and data origin are separate attributes (2026-11-10)
`kind` (raw, background, net, processed) and `origin` (experimental, synthetic) are
orthogonal, so a synthetic background or synthetic net spectrum is representable and can
never be confused with a measurement. The specification's "synthetic spectrum" category
is `origin == SYNTHETIC`. Mixing origins in one calculation is refused.

## TD-010 Core objects are frozen dataclasses; boundary objects are Pydantic (2026-11-10)
`Spectrum`, `SourceFile`, `ProcessingStep`, `Quantity` are frozen dataclasses holding
read-only NumPy arrays (owned copies) plus a data hash taken at construction;
`verify_integrity()` detects deliberate tampering. Calibration records, detector
profiles, settings and synthetic specifications are frozen Pydantic models because they
cross serialisation boundaries.

## TD-011 Only formats seen in real files have adapters (2026-11-10)
Lynx CSV, Lynx XML (.ava), Canberra CAM (.cnf), plus NSW's own synthetic JSON. Generic
CSV/TXT, SPE, CHN and MCA adapters planned in Phase 0 were *not* written: without real
files their structure would be assumed. Such files are rejected explicitly. Supersedes
the adapter list in docs/design/architecture.md.

## TD-012 Measurement identity from content (2026-11-10)
Snapshot = identical channel data; acquisition = start times within 1 s; a series is
cumulative if no channel decreases. Only the final save of a cumulative series is an
independent measurement. Details and limits: docs/science/scientific_methodology.md section 4.

## TD-013 Whole-second Lynx CSV files are treated as re-exports of CNF files (2026-11-10)
Direct Lynx CSV exports carry microsecond real times. Seven CSV files carry whole-second
times instead, as CNF files do, and three of those also carry a start time 8 h 47 min
later than the CNF holding the same data. When both exist for the same data the CNF is preferred as
the record of start time. To be confirmed by the laboratory.

## TD-014 Background role is an explicit analyst designation (2026-11-10)
No spectrum is classified as background from its file or sample name. Designation
creates a new BACKGROUND-kind object with a history entry; only independent measurements
can be designated. Designations are session state until project storage exists.

## TD-015 Correlated scaling uncertainty kept out of the per-channel variance (2026-11-10)
The uncertainty of alpha from rounded live times is common to all channels. It is
reported separately and enters region sums as u(alpha) * sum(B), instead of being added
in quadrature per channel, which would understate it for sums.

## TD-016 Channel axis only in the viewer (2026-11-10)
Until an energy calibration has been fitted and verified (Phase 6) the plot has no
energy axis at all, including for synthetic spectra whose true scale is known. One rule,
no exceptions, no misleading labels.

## TD-017 Settings live in `nsw.config`, outside the layers (2026-11-10)
Phase 0 planned `nsw.core.settings`; reading YAML from the core would break the rule
that scientific packages do no file or configuration access. A test enforces the
layering by inspecting imports.

## TD-018 Synthetic lines use arbitrary energies (2026-11-10)
Synthetic scenarios use round test energies instead of nuclide lines, so no nuclear
reference value is introduced before a traceable data source is selected (Phase 9).

## TD-019 Peaks are fitted as channel-integrated Gaussians, parameterised by area (2026-11-11)
The model value of a channel is the integral of the Gaussian over the channel. The
density form G(x) = A exp(…) of the specification is biased by 1/(24σ²) in σ for
histogram data (1.4 % at FWHM 4 channels, 20 standard deviations for a 2 × 10⁶-count
peak). The area N is a fitted parameter, so its uncertainty comes straight from the
covariance; A = N/(σ√2π) and FWHM = 2.35482 σ are derived.

## TD-020 Poisson likelihood is the default for counts; least squares only for net data (2026-11-11)
Monte Carlo (VALIDATION_REPORT section 2): least squares with 1/n weights is biased by
+2 % at 8 counts per channel and cannot use empty channels; the likelihood fit is
unbiased down to 0.05 counts per channel. No pseudo-counts anywhere. The Poisson
likelihood is refused for net spectra.

## TD-021 Joint gross + background fit by profile likelihood (2026-11-11)
For independent gross and background measurements the background expectation of each
channel is profiled out analytically instead of subtracting first. The source continuum
is left unconstrained in sign (constraining it to ≥ 0 biased areas by −2.3 % when the
true continuum is zero). Documented weakness: under-coverage when the background has
fewer than about 5 counts per channel and was not measured longer than the sample.

## TD-022 One minimiser: damped Fisher scoring; covariance from expected information (2026-11-11)
A single ~60-line routine serves all three estimators (score and information weight per
channel differ). SciPy's `least_squares` on deviance residuals was rejected: its
Gauss–Newton curvature is wrong for empty channels (half the Fisher information) and the
residual √(2m) has an infinite derivative at m → 0. Uncertainties are not rescaled by
√(χ²/ndf); a bad χ² is reported as POOR FIT instead of being absorbed.

## TD-023 Goodness of fit at low counts from exact deviance moments (2026-11-11)
When any fitted expectation is below 5 counts, χ² tables are replaced by a comparison of
the deviance with its exact Poisson mean and variance (normal approximation). Measured
false-alarm rate at p < 0.05: 0.038–0.045 down to 0.05 counts per channel.

## TD-024 "Resolved" is never inferred from convergence (2026-11-11)
A multiplet is assessed by ΔBIC against the best fit with one component fewer, requires
an unflagged and statistically acceptable full fit, and is worded as statistical support
only. No likelihood-ratio p-value is quoted (boundary problem).

## TD-025 Zero-area second-derivative filter for the peak search (2026-11-11)
Chosen over `scipy.signal.find_peaks` on raw or smoothed counts because the significance
has a defined variance, the continuum drops out without a baseline estimate, and width
and area follow from the responses at two scales. `find_peaks` is used only to pick
local maxima of the significance.

## TD-026 Peak pages keep the channel axis; energies appear only as table columns (2026-11-11)
TD-016 stays in force for all plots. Tables and CSV exports show an energy column only
for spectra with a verified calibration (currently synthetic truth only). Results from
experimental spectra carry the label "EXPERIMENTAL, PROVISIONAL — channel units".

## TD-027 Graphify graph is code-only and local (2026-11-11)
Built from the AST pass only (888 nodes); the model-driven extraction of the Markdown
documents was skipped to save tokens, since the documents are read directly. The
experimental dataset lies outside the project root and `data/synthetic` is git-ignored,
so neither is indexed. `graphify-out/` is git-ignored. Graphify runs under the Anaconda
interpreter where it is installed; project code still uses `.venv` only.

## TD-029 Calibration by effective-variance regression in a scaled basis (2026-11-11)
Channel and reference-energy uncertainties both weight the fit
(v = u_E² + (E′·u_ch)², iterated). The fit is done in t = (ch − mean)/half-span and
transformed exactly to powers of channel; the stored record keeps plain coefficients
and covariance. Agreement of the two representations is tested to 1e-5 up to channel
8500. Orthogonal-distance regression was not needed: the model is nearly linear and the
effective variance is exact to first order.

## TD-030 A verified fitted calibration needs a redundant point (2026-11-11)
The record model (`nsw.core.calibration`) now requires more points than coefficients
for VERIFIED (was: at least as many). A curve through exactly as many points as
coefficients cannot be checked against anything.

## TD-031 Experimental calibrations are provisional by a service-level switch (2026-11-11)
`services.calibration.EXPERIMENTAL_VERIFICATION_ENABLED = False`. The engine would accept
the points; the policy does not, until the project owner confirms line assignments and
a traceable source of reference energies (project rule). The switch is one constant so the
decision is visible and testable. No reference energies are shipped with NSW before
Phase 9 selects a nuclear-data source.

## TD-032 Uncertainties are inflated only on request (2026-11-11)
Multiplying the covariance by χ²/ndf (Birge ratio) is an explicit analyst choice,
recorded in the fit, the record's method text and the export. It is never applied
when χ²/ndf ≤ 1 and never silently.

## TD-033 Fit results and calibration history live in the shared application state (2026-11-11)
The calibration page needs the fits made on the fitting page, so both are held in
`AppState` (signals `fits_changed`, `calibrations_changed`). Still session-only;
persistence comes with the SQLite project store (Phase 11).

## TD-034 Resolution model fitted on FWHM, constraints by subset enumeration (2026-11-11)
FWHM² = a + bx + cx² is linear in the parameters only for W², whose noise bias (+u²) is
avoided by Gauss–Newton on W. With at most three terms the non-negativity constraints
are solved exactly by fitting all subsets (≤ 7 fits) instead of a general bounded
optimiser. One functional form for both detector types; separate fitted models per
detector. `ResolutionCalibration.points` reuse `CalibrationPoint` with `channel` holding
the abscissa (keV or channel, named in `fwhm_unit` and the method text).

## TD-035 Fixed parameters are marked by lower = upper (2026-11-11)
`FitSettings.fixed_centroids` / `fixed_width` hold values by setting equal bounds; the
minimiser never frees such a parameter, its reported uncertainty is 0, it is not counted
in ndf, and every result says that the fixed value's uncertainty is not propagated.
Widths that were held fixed are excluded from resolution analysis.

## TD-036 Cyclic garbage is collected on the GUI thread only (2026-11-11)
`create_application` disables automatic garbage collection and starts a one-second
timer that calls `gc.collect()` on the GUI thread; `workers.start` collects before
starting a worker. Reason: a collection that starts in a worker thread can destroy Qt
objects outside their thread and deadlock (observed and diagnosed in Phase 7).
Consequence: in a process that created the application object, garbage is freed at
most one second late; memory use of the test process is slightly higher.

## TD-037 Efficiency and activity in the logarithm, with explicit correlations (2026-11-11)
The efficiency curve is ln ε polynomial in ln E, fitted by generalised least squares
with the full point covariance; the activity uncertainty of a source is one fully
correlated term for all its lines. Activities from several lines are combined with the
curve's covariance between their energies. Treating the points as independent would
understate the uncertainty of an activity by averaging away the certificate error;
the Monte Carlo test with a fluctuating certificate value confirms correct coverage.

## TD-038 No experimental efficiency or activity: one service-level switch (2026-11-11)
`services.efficiency.EXPERIMENTAL_QUANTIFICATION_ENABLED = False`; the service refuses
experimental spectra and returns the list of missing inputs. Same pattern as TD-031.

## TD-039 Synthetic sources are fictitious nuclides (2026-11-11)
`nsw.simulation.sources` defines SYN-CAL and SYN-X with round, arbitrary energies,
emission probabilities and half-lives, and an arbitrary efficiency model. The
simulation layer re-implements the decay law instead of importing the analysis layer
(architecture rule); a test compares the two. No real nuclear data are introduced
before Phase 9 selects a source.

## TD-040 Efficiency and activity pages deferred to Phase 11 (2026-11-11)
Phase 8 delivers engines, service gate and validation. The two GUI pages need source
and nuclear-data entry forms that belong with the project store (sources and reference
data must be saved, not retyped); they are built in Phase 11.

## TD-028 Master specification v3.0 authorises Phases 5–12 without per-phase approval (2026-11-11)
The user's specification of 2026-11-10/11 replaces the earlier stop-after-each-phase
rule. Work still stops for missing experimental information, unresolved scientific
assumptions, failing critical tests, destructive actions and external operations.

## TD-041 Nuclear data: DDEP primary, ENSDF supplementary, never merged (2026-11-11)
Decision of the project owner. Each `NuclideRecord` belongs to one evaluation; a library
may hold both for a nuclide and an analysis picks one by an explicit preference order.
IAEA LiveChart and NuDat are interfaces to ENSDF and are refused as evaluation names.

## TD-042 No nuclear data bundled; import by file only (2026-11-11)
Neither lnhb.fr nor nndc.bnl.gov states redistribution terms (checked 2026-11-11), so
NSW downloads and ships nothing and has no DDEP/ENSDF parser (no real file inspected).
Users import an NSW reference-data file with full provenance (`docs/science/nuclear_data.md`).

## TD-043 Intensity convention is part of every line (2026-11-11)
Only `photons-per-decay` is an emission probability; `relative` intensities are used
for energy matching alone and cap a candidate at "possible". Per-decay values above 2
are refused as probable percentages.

## TD-044 Identification yields candidates with classes, never "identified" (2026-11-11)
Window k·sqrt(u_peak² + u_line² + u_cal² + u_extra²), k = 3 exposed in the settings.
Classes: not supported, possible, probable, consistent. One matched peak is at most
"possible"; "consistent" needs an efficiency curve and a passed intensity test. A
nuclide whose peaks are all explained by a candidate with more peaks is capped at
"possible". Rules and score are defined in the module docstring.

## TD-045 Identification gate (2026-11-11)
`services.identification` refuses spectra without a VERIFIED energy calibration,
fictitious libraries on measurements and real libraries on synthetic spectra.

## TD-046 Approximate Currie limit only for the missing-line judgement (2026-11-11)
L_D = 2.71 + 4.65·sqrt(B) over a 2.5 FWHM window decides whether an absent line counts
as missing. It is not reported as a detection limit of the measurement.

## TD-047 Public repository: nothing pushed without approval; history not rewritten in place (2026-11-11)
Audit findings are fixed in the current files; removal from history is proposed as a
rewritten clone, leaving the working repository as the private original.

## TD-048 Corrections after the independent review (2026-11-11)
Candidate ranking: only a probable or consistent candidate subsumes another; lines
outside the efficiency curve carry no weight; two peaks matched by lines of known
emission probability are needed above "possible"; unresolvable peaks count once; the
anchor area is shared by all lines on the anchor peak. Gates: a verified calibration is
never attached to an experimental spectrum, and the identification service refuses
experimental spectra, while `EXPERIMENTAL_VERIFICATION_ENABLED` is False; fits must
carry the spectrum's data hash. Evaluation names are stored trimmed and upper-case.
Open findings: `docs/reports/REVIEW_PHASE8_9.md`.

## TD-049 Validation suite: checked versus characterised metrics, 5 standard errors (2026-11-11)
A metric with an ideal value passes or fails against a five-standard-error tolerance;
behaviour that is a known limitation (bias of weak free-shape fits, detection near the
threshold) is reported as characterised and can never count as passed. Results carry a
SHA-256 digest: the same seed must reproduce it exactly.

## TD-050 Project file: SQLite, append-only, reproduction on reopening (2026-11-12)
One file per project; results are never updated or deleted (database triggers).
Spectra are stored completely so a project can be reopened when the source folder is
gone. Fits and calibrations are not trusted from the file: they are recomputed from
the stored inputs and must agree (exactly when the starting values are recorded).

## TD-051 Reports drawn with Matplotlib without pyplot; no timestamps in the files (2026-11-12)
No new dependency (Matplotlib was already declared). Identical input gives an identical
PDF, so reports can be compared and archived by hash. Tables too wide for the page are
written as one block per row instead of being cut.

## TD-052 Windows build: PyInstaller one folder, verified by a built-in self-test (2026-11-12)
One folder (not one file) keeps the Qt libraries replaceable (LGPL) and starts faster.
Because the test suite is not shipped, `--self-test` exercises the real window on
synthetic data and is the acceptance check for a build. LGPL/GPL texts must be added
before any distribution (not yet done; nothing distributed).

## TD-053 Start-up: heavy modules imported on use; probability functions from scipy.special (2026-11-12)
Matplotlib (reports), the PyQtGraph exporter (PNG export) and `scipy.signal` (peak
search) are imported inside the functions that need them. `scipy.stats` is no longer
used by the application: `nsw.analysis.distributions` computes the chi-square and normal
survival functions and the Poisson probability with `scipy.special` (`chdtrc`, `ndtr`,
`xlogy`/`gammaln`), tested against `scipy.stats` to a relative 1e-12 (Poisson 1e-10)
over grids including the far tails. Import of the main window: 8.8 s → 3.2 s; main
window shown: 9.1 s → 3.6 s (`scripts/dev/measure_startup.py`, warm runs). The remaining
time is mostly `scipy.special`/`scipy.linalg` (0.8 s), pydantic models and PyQtGraph.

## TD-054 Dependencies locked with uv; pandas removed (2026-11-12)
`uv.lock` (46 packages) is committed; development, CI and builds install with
`uv sync --locked --all-extras` and dependency updates are deliberate commits that pass
the test suite and the validation runner. pandas and pandas-stubs were declared but
never imported (searched in `src`, `tests`, `scripts`, packaging and docs; no installed
package requires pandas) and were removed: −13 MB pandas and −1.5 MB tzdata in the
previous build folder (269 MB).

## TD-055 Pre-publication scanner; real file names and a time stamp removed from tests (2026-11-12)
`scripts/release/scan_public_copy.py` searches a public candidate folder (text and binary
files, except Git-ignored ones) and every reachable Git blob and commit for private
patterns kept in git-ignored `local/public_scan_patterns.txt` (dataset folder name,
owner identifiers, private dataset file names of 6+ characters, the acquisition time
stamps found below) and for generic credentials, user-profile paths and non-no-reply
e-mail addresses; private matches are reported by number, never echoed; exit 1 on a
finding. Its first run on the public repository found what the manual searches had
missed: six real dataset file names in a duration-parsing test, one real file name as a
sample name, and the real acquisition time stamp of one file (tests, two docstrings,
`docs/user/file_formats.md`). They were replaced in this repository by invented names and the
invented time `Tue Mar 10 09:15:27 GMT+0530 2026`. The public repository still contains
them in its working tree and history; the next approved transfer removes them from the
tree. Rewriting public history is not proposed (low sensitivity; it would break the
published tag) unless the owner asks for it.

## TD-056 Security rules, test tools, Gitleaks, Inno Setup (2026-11-12)
Ruff's `S` (Bandit) rules are enabled. Exceptions are individual and explained in the
line: type-narrowing `assert`s in analysis code (each states the check that guarantees
it), the AVA parser's `ElementTree` call (DOCTYPE and ENTITY are refused before parsing),
subprocess calls with fixed argument lists, and `assert` in tests (`tests/**`, S101 only:
it is pytest's mechanism). Two interface `assert`s became explicit `IndexError`s.
Development extra: Hypothesis (7 property tests of exact laws: decay composition,
background subtraction, linear calibration, distribution functions), pytest-qt
(`qt_api = pyside6`), pytest-timeout (900 s per test), pip-audit (no known
vulnerabilities). pytest-qt now fails a test when an exception is raised inside a Qt
callback; this exposed a harmless pyqtgraph 0.14.0 bug (`LabelItem.sizeHint` called
during construction), for which only the self-test test is exempted.
Owner-approved downloads: Gitleaks 8.30.1 (official GitHub release, SHA-256 checked
against the release checksum file; kept in `%LOCALAPPDATA%\nsw-tools`, outside the
repository): no leaks in the Git history of either repository; working-tree hits are
NumPy test data inside the git-ignored `.venv`. Inno Setup 6.7.3 installed per user via
winget (installer hash verified by winget) for the Windows installer (Stage 10).
While writing the property tests, two more real acquisition times were found in a
validation test and replaced; every start time in the dataset's CSV files is now a
private scan pattern, so this class of leak is caught automatically.

## TD-057 Continuous integration prepared (R6) (2026-11-12)
`.github/workflows/test.yml`: Windows / Python 3.12 and Ubuntu / Python 3.11; read-only
token, no secrets, `pull_request` only, actions pinned to full commit SHAs
(checkout v7.0.1, setup-uv v10.3.3, upload-artifact v7.0.2), install with
`uv sync --locked --all-extras`; Ruff, format, MyPy, pytest with coverage, pip-audit,
the generic part of the public-copy scan, and on Windows the 1000-trial validation with
repeatability (tables kept 30 days). `.github/dependabot.yml`: weekly uv and Actions
updates; test tools grouped, scientific and GUI libraries one pull request each.
Every command was run locally, including a separate Python 3.11 environment synced from
the lock (478 passed). On 3.11 the lock resolves older NumPy/SciPy whose type stubs
gave five MyPy errors; fixed with explicit `float64` conversions and `tobytes()` for
hashing (same bytes: validation digest `bc4d8bace3e63f7c` unchanged). Not yet run on
GitHub's runners: the Linux Qt library list in particular is unverified until the first
run. CodeQL, secret scanning and push protection are repository settings for the owner.

## TD-058 Decision thresholds and detection limits: two selectable methods (2026-11-12)
Owner's request: the analyst can choose between the ISO 11929 framework and the
classical Currie limits. `nsw.analysis.limits` implements both for counting models
(`CountingModel`: background, its variance, conversion factor w and u_rel(w)), the ISO
coverage interval with the prior ỹ ≥ 0, and refuses settings outside 0 < α, β, γ < 0.5.
Default `iso11929` with α = β = γ = 0.05 (approved), configurable in
`analysis.detection_limits` of the settings file; every result will record its method.
A zero-background case exposed a spurious fixed point y# = 0 in the ISO iteration; the
iteration now starts at k²·ũ(1)², which is the exact answer there. Verified: Currie's
2.71 + 4.65√B, equality of both methods for exact w, the closed form with uncertain w,
non-existence for k·u_rel(w) ≥ 1, and Monte Carlo false-positive and detection rates
(4 × 10⁵ trials). Conformity with ISO 11929 is not claimed.

## TD-059 Regions of interest as a service; project schema 2 with a decision log (2026-11-12)
`nsw.services.roi` (Qt-free): `Roi` (range, kind single/multiplet/custom, peaks with
starting widths, stable id), validation, `propose_rois` (one ROI per overlap group of the
search, 3 FWHM continuum margin, pairs closer than 1.5 estimated FWHM reported as
possibly unresolved — owner-approved defaults), `net_area` (gross minus a straight
continuum through the mean of the edge channels, with the continuum's own variance;
exact for linear continua, Monte Carlo pull sd within 0.05 of 1), `limits` (either
method of TD-058), `fit_roi`, and `record` / `current_rois`. The close-pair rule uses the
search's width estimates, which are low for blended peaks (3.7 instead of 5 channels in
the test); widths from the resolution model (R12) will replace them.
Project files are now schema 2: an append-only `decision_event` table. Schema-1 files
are copied to `<name>.before-schema-2.<time>.nswproj` and migrated in one transaction;
the migration is tested on a file written exactly as schema 1 wrote it.

## TD-060 Interactive plot (U2): modes, regions with undo, Regions panel (2026-11-12)
`SpectrumPlot` uses a `SpectrumViewBox` that routes region drags, clicks and right-clicks
to the plot; four modes (navigate, zoom box, region, select) with toolbar and keys
(Esc, Z, R, S; Home, L, Del, F, Ctrl+Z/Y; shortcuts limited to the plot). The plot only
emits signals; `RoiController` validates gestures through `services.roi`, applies them
as `QUndoCommand`s (one `QUndoStack` per spectrum), keeps regions in `AppState`, and
redraws refused edits back. `RoiPanel` lists regions with net counts and limits and
offers the ISO 11929 / Currie choice (default from the settings file). Close pairs in
region proposals now use the resolution model's widths when one is available
(`fwhm_from_resolution`), resolving the low width estimates for blended peaks; models
in keV are refused rather than converted without a verified calibration.
Tests: 7 pytest-qt tests with synthesised mouse events (press/move/release on the plot
viewport) and key clicks. Not yet done: regions saved to the project file from the
interface (the service and schema exist), and a manual walk-through at 1366×768 and
1920×1080.

## TD-061 Fitting from a region and nested model comparison (U3) (2026-11-12)
A region is fitted with its own peaks, else the candidates of the spectrum's last peak
search inside it, else a search restricted to the region (scales from the spectrum's
width estimate); analysts add peaks from the context menu (undoable; width from the
region's other peaks or the estimate). Results join the spectrum's fits in `AppState`,
so the Peak Fitting page and reports see them. *Compare models* fits constant ⊂ linear ⊂
linear + step continua with the same estimator and range and keeps the simplest
converged model unless the decrease of the fit statistic is significant (chi-square
with the change of ndf, p < 0.01; halved for the step, whose fraction is bounded at 0).
Tested: a flat continuum keeps "constant", a steep slope selects "linear". The tail
model (R7) will join this comparison once implemented.

## TD-062 Automatic peak search on opening; candidate quality classes (U4) (2026-11-12)
Measured on a 16 384-channel spectrum with 60 peaks: width estimate 19 ms, search 8 ms
(59 found), but the first search costs 1.7 s for importing `scipy.signal`. The search
therefore runs automatically when a spectrum is opened (switchable), and `scipy.signal`
is imported in a background thread right after the main window is shown (import only;
start-up unchanged, the start-up test still passes). `services.candidates` classifies
candidates: strong (S ≥ 10), weak, doubtful (within 1 σ of the threshold, or width
ratio outside 0.5–1.6), not photopeak-like (width > 2.5 × expected). Expected width:
the resolution model when given, else the median width of the strong candidates (said
in the reasons). Not-photopeak-like and analyst-excluded candidates are left out of
region fitting; exclusions are undoable and kept per spectrum in `AppState` (not yet
saved to the project file).

## TD-063 Nuclear Data Explorer page, gated overlay, peak matching (U5) (2026-11-12)
Libraries loaded in the session now live in `AppState.libraries` (fictitious library
always; files loaded from either page are shared). `services.nuclear_explorer` gives the
nuclide, line, provenance and evaluation-difference tables, the overlay gate (the same
rules as identification: verified calibration, experimental verification, fictitious
library only for synthetic spectra and real libraries only for measurements), line
positions by numerically inverting the verified calibration over the spectrum (refused
if it does not increase with channel), and `match_peak`: lines within
|ΔE| ≤ k·√(u_peak² + u_line² + u_cal²), k = 3, ranked by the fraction of the nuclide's
other strong lines (≥ 50 % of its strongest, inside the spectrum) that have a fitted
peak, then |ΔE|/σ. Accept/reject decisions are kept per spectrum in `AppState` (saving
to the project's decision log comes with the provenance work). The page is inserted
before Nuclide Identification: page indices 9 → Nuclear Data Explorer, 10 →
Identification, 11 → Project File (constants and the self-test updated).

## TD-064 Assisted energy calibration (U6) (2026-11-12)
From an analyst-entered approximate linear calibration (gain, offset, relative
uncertainty r), each fitted peak's compatible library lines are those with
|E_approx − E_line| ≤ 3·√((r·E_approx)² + (g·u_ch)² + u_line²). Only a peak with exactly
one compatible line is filled in automatically (labelled "suggested"); ambiguous peaks
are listed. Vendor calibrations are never used as the guess (inventory §5). Before the
fit NSW warns about duplicate assignments, too few points and coverage below 30 % of the
channels; after it, about points with |normalised residual| > 3. Library kind rules as
elsewhere. Measured spectra stay PROVISIONAL. Tested on the synthetic calibration source:
every automatic suggestion is the true line; ambiguous peaks list it.

## TD-065 Provenance: decisions saved, result digests, no source paths (2026-11-12)
Implements the approved provenance design without a further schema change: every
result saved through `services.project` gets a `provenance` block in its settings
(algorithm version per result kind, NSW/Python/NumPy/SciPy versions, SHA-256 of the
canonical JSON of the stored result, `store.canonical_digest`). Reopening a project
recomputes every digest and reports mismatches next to the existing reproduction of
fits and calibrations (a tampered fit is now caught by both checks). Saving a session
also appends the analyst decisions not yet in the project: region creations, edits and
deletions (against the replayed regions), the set of excluded candidates when it
changes, and each accept/reject of a line assignment once; reopening restores them.
Source files are stored with their file name only (the full path could contain the
user's name; owner decision); the bit-for-bit round-trip test now expects this.
A separate provenance table and per-analysis algorithm constants remain possible; the
settings block was chosen because it needs no migration and is append-only already.

## TD-066 Guided workflow panel and beginner / advanced modes (U7) (2026-11-12)
`services.workflow` (Qt-free) derives the status of the twelve stages from a
`WorkflowContext` the main window fills from `AppState` and the selected spectrum;
blocked stages carry the same reasons as the gates (measured efficiency/activity need
certified source data; identification needs a verified calibration). `WorkflowPanel`
sits under the page list, refreshes on state changes, opens a stage's page on click and
names the next ready stage. Modes only change visibility: widgets marked advanced
(continuum channels, model comparison, sensitivity, covariance scaling, order test,
assistance uncertainty) are hidden in Beginner mode, the default. The mode is not yet
remembered between sessions.

## TD-067 R7: limits from peak fits; optional HPGe tail model (2026-11-12)
Limits from a fit use ũ(Ñ) from the expected Fisher information of the Poisson fit
model at a hypothetical true area (continuum and other peak areas as nuisance; position
and width fixed); Currie mode uses σ0 = ũ(0). Validated by Monte Carlo (both methods).
The tail model lives in `analysis.tailfit` (L-BFGS-B on the Poisson deviance, bounded
η, β; covariance from the expected information with a numerical Jacobian) so that the
existing Gaussian fits and the validation digest are untouched; model choice by a
parametric-bootstrap 99 % threshold. Status and open items: the "Implementation
status" sections of the two design documents.

## TD-068 Windows installer; layout for 1366 × 768 (2026-11-12)
Per-user Inno Setup 6 installer (`packaging/windows/installer.iss`, fixed AppId for
upgrades, `{autopf}` with lowest privileges, licence page, Start Menu entries incl.
self-test, demo spectra and licences, optional desktop shortcut, launch after install;
the uninstaller removes only installed files). The spec moved to
`packaging/windows/NSW.spec` and bundles `build/licenses` from
`scripts/release/collect_licenses.py` (licence files of the 22 runtime packages, verbatim
LGPL-3.0 and GPL-3.0 texts taken from local installed packages, Python licence, Qt
replacement notice; nothing downloaded). The installed application reads its settings
from `%LOCALAPPDATA%\NSW\local.yaml`. `scripts/release/build_windows.ps1` builds from the lock,
self-tests the built folder, compiles the installer and writes `SHA256SUMS.txt`;
`scripts/release/smoke_test_installer.ps1` installs silently to a temporary folder, self-tests,
reinstalls, uninstalls and checks that user data remain. Not signed.
`scripts/dev/ux_screenshots.py` showed a minimum window width of 1579 px; rows were split,
pages put in scroll areas, the Regions tab made scrollable and the spectrum plot given a
minimum height (docs/reports/UX_TEST_REPORT.md).
