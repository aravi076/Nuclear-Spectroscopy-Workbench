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
the adapter list in docs/architecture.md.

## TD-012 Measurement identity from content (2026-11-10)
Snapshot = identical channel data; acquisition = start times within 1 s; a series is
cumulative if no channel decreases. Only the final save of a cumulative series is an
independent measurement. Details and limits: docs/scientific_methodology.md section 4.

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
Users import an NSW reference-data file with full provenance (`docs/nuclear_data.md`).

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
