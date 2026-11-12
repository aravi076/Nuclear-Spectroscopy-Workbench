# Scientific Provenance Design

Status: **Approved by the owner on 2026-11-12**, including the decisions listed at the end.
the Master Development Directive and recommendation R9.

## 1. Goal

For every reportable number NSW produces, a reader can answer: *from which data, with
which settings, reference data, calibration and analyst decisions, by which software
and algorithm version* — and NSW can **recompute** it and confirm the same value.
Reproduction, not just description, is the test.

## 2. What exists today

| Element | Where | Recorded today |
|---|---|---|
| Spectrum identity | `Spectrum.data_sha256` (counts, dtype, variance) | yes; checked on every read from a project |
| Source file | `SourceFile` (full path, file name, size, SHA-256, format, adapter), stored in the project's `measurement.record` | yes — including the **full path**, which may contain a user-profile folder |
| Measurement metadata | `Spectrum` (live/real time, start time, detector id, origin, kind) | yes |
| Background designation | `background_event` table (append-only) | yes |
| Peak search, fits | `analysis_run` (`kind`, `settings` JSON, `result` JSON, `software`, `created`) | yes; fits record starting values and are recomputed on reopening |
| Calibrations | `calibration` table (record JSON, status, synthetic flag, software) | yes; recomputed on reopening |
| Reference data | `reference_dataset` (document, SHA-256, evaluations, fictitious flag) | yes |
| Project actions | `audit_log` | coarse |
| ROI, accepted/rejected candidates and assignments, model comparisons | — | **no** |
| Links between results (fit → calibration point → calibration → activity) | — | **implicit only** |
| Algorithm version distinct from software version | — | **no** |

## 3. Model

Every reportable result becomes a **provenance record** that refers to its inputs by
content hash or record id, never by copying them:

```text
ProvenanceRecord
  result_id          uuid4
  kind               peak_search | fit | energy_calibration | resolution | efficiency |
                     activity | identification | report
  inputs             list of (role, ref)  e.g. ("spectrum", sha256), ("background", sha256),
                     ("fit", result_id), ("calibration", id), ("reference_library", sha256)
  settings           canonical JSON of every parameter that influences the number
  roi                (first_channel, last_channel) or null
  decisions          list of decision ids that shaped this result (accepted candidates,
                     assignments, model choice)
  algorithm          {"name": "fit_peaks", "version": "fitting/3"}   # bumped on any change
                                                                      # that can alter output
  software           NSW version, Python, NumPy, SciPy versions
  random_seed        when the computation uses one (validation, Monte Carlo)
  result_digest      SHA-256 of the canonical JSON of the numeric result
  uncertainty_budget statistical and systematic components, separately
  labels             SYNTHETIC | EXPERIMENTAL | EXPERIMENTAL, PROVISIONAL
  created            UTC timestamp (informational; never part of a digest)
```

**Algorithm versions** are constants next to each analysis function
(`ALGORITHM_VERSION = "fitting/3"`), changed by the developer when an output can change.
A stored result whose algorithm version differs from the current one is reproduced with
a tolerance and the difference is reported, instead of failing silently.

**Canonical JSON**: sorted keys, floats with `repr` precision, no timestamps — the same
rule that makes reports byte-identical today (TD-051).

**Decisions** are their own append-only records:

```text
DecisionEvent (decision_id, at, subject_ref, action, value, note)
  action ∈ roi_create | roi_edit | roi_delete | candidate_add | candidate_exclude |
           candidate_restore | assignment_accept | assignment_reject | model_select |
           calibration_point_use | calibration_point_drop
```

## 4. Storage (schema v2)

Additive migration of the project file:

- `provenance` (result_id PK, kind, inputs JSON, settings JSON, roi, decisions JSON,
  algorithm, software JSON, random_seed, result_digest, uncertainty JSON, labels,
  created) — append-only trigger as for the existing tables;
- `decision_event` — append-only;
- `analysis_run` and `calibration` rows gain a `result_id` link (new nullable column);
  v1 rows get provenance records synthesised on migration from what they already store,
  marked `migrated_from_v1`.

Migration rules: a backup is made first (existing `backup` function); the schema
version moves 1 → 2 in one transaction; v1 files remain readable by v2 code; v2 files
are refused by v1 code with a clear message (application id stays `NSW1`, version
check already exists).

## 5. Reproduction

`services.project.reproduce(result_id)`: loads the inputs by reference, re-runs the
algorithm with the stored settings and decisions, compares `result_digest`; on a
mismatch, reports the largest relative difference per quantity and whether the
algorithm version changed. The Project page gets *Verify all results*, and every report
gets a "Reproducibility" section listing each result with its digest and verification
status.

## 6. Reports

The PDF report's provenance section lists, per result: inputs (file names and
SHA-256 prefixes), settings, ROI, decisions, algorithm and software versions, reference
data with evaluation and retrieval date, labels, and the uncertainty budget split into
statistical and systematic parts.

## 7. Privacy

A project file contains the channel counts of every spectrum it holds (needed for
reproduction), so a project with experimental spectra is itself private data. Reports
and provenance records reference files by name and hash and never print counts of
experimental spectra. Example projects in the public repository use synthetic spectra
only. Today `SourceFile.path` stores the full path; the proposal is to store only the
file name and hash (and optionally a path relative to the project), because a
user-profile path in a shared project leaks the user name.

## 8. Testing

- Round trip: create → save → reopen → `reproduce` gives identical digests for every
  kind of result (synthetic data).
- Mutation tests: changing one setting, one decision or one input hash changes the
  digest; changing only `created` does not.
- Migration: v1 fixtures (existing test projects) migrate, keep their numbers, and
  reproduce.
- Append-only: updates and deletes on the new tables are refused by triggers.

## 9. Size and order

Medium. Order: algorithm version constants and canonical digests (no schema change) →
schema v2 with decisions (needed by ROI work, U1 in `docs/design/UX_REDESIGN_PLAN.md`) →
provenance records for every result kind → reproduction and report section.

## 10. Decisions requested from the owner

1. Approve the model and the schema v2 migration.
2. Agree that file paths are not stored in project files (only names and hashes), at
   the cost of re-locating source files manually when a project moves.
