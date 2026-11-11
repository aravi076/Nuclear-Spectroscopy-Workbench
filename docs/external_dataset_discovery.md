# External Open-Data Discovery Plan

Status: plan plus first leads (2026-11-10). **Nothing has been downloaded, imported or
relied upon.** Every entry below is an unverified lead from a brief literature/web
search; the "to verify" column lists what must be confirmed before any use.

## Why external data

1. Experimental NaI(Tl) spectra: the laboratory dataset has none.
2. HPGe spectra with *documented* source activities and geometry, to test efficiency and
   activity code that the laboratory dataset cannot exercise.
3. Additional file formats (SPE, CHN, N42, …) to justify new import adapters.

## Acceptance criteria for a dataset

A dataset may be used only if all of the following are recorded in
`data/reference/external/<name>/PROVENANCE.md`:

- persistent identifier (DOI) and repository; authors; associated publication;
- licence that permits the intended use (CC BY / CC0 or equivalent); citation text;
- detector type and, ideally, model and crystal size;
- whether spectra are **measured or simulated** (simulated data stay in the synthetic
  category);
- file format, with the format documented or verifiable against a second parser;
- acquisition metadata: live/real time, date, source identity; for quantitative use
  also activity, reference date, uncertainty and geometry;
- retrieval date and SHA-256 of every file retrieved.

External data are stored outside Git, separately from both the laboratory data and
NSW's synthetic data, and are labelled by origin in the application.

## Leads found

| Lead | Type (as described by the source) | Why interesting | To verify before use |
|---|---|---|---|
| IAEA test spectra for gamma-spectrometry software (1995/1997 set; 2002 low-level set with certified sources in Marinelli and pillbox geometry) — described in NIM A and related papers; an old public address at TU Delft is cited | Measured HPGe spectra made for software intercomparison, with reference values | The closest thing to a recognised benchmark for peak-area and activity software | Whether the files are still obtainable from an official IAEA or author source; licence; file format; exact reference values |
| Zenodo record 3911859 (2020) | Labelled example gamma peaks from an HPGe detector, part real, part Geant4 | Peak-shape test cases | Contents are 13-bin peak snippets, not full spectra — may be unsuitable; licence; detector details |
| Illinois Data Bank IDB-4860767 | Gamma-ray spectrum templates for source interdiction / enrichment, tagged NaI(Tl), CC BY | NaI(Tl) spectra with a clear licence | Measured or simulated; format; metadata per spectrum |
| Dryad doi:10.7941/D1XC97 (RADAI project) | **Simulated** urban NaI(Tl) spectra, ~2.3 GB HDF5 | Large NaI data volume | Simulated → synthetic category only; size vs. the 15 GB allocation; relevance |
| arXiv 2508.08306 benchmark | **Simulated** 3"×3" NaI(Tl) spectra (Geant4), nine radionuclides, experimental background | Identification benchmark | Where the data are hosted; licence; simulated → synthetic category only |
| Mendeley Data xmmgbrfgnb | **Simulated** LaBr₃(Ce) spectra | Not HPGe or NaI | Probably out of scope |

No lead has yet been confirmed as *measured NaI(Tl) spectra of standard sources with
documented acquisition conditions*. That remains the main gap.

## Next search steps (in order)

1. IAEA: publications and Nuclear Data Services pages for the software-intercomparison
   test spectra; ask the laboratory whether it already holds a copy.
2. Zenodo / Figshare / Mendeley Data / Dryad, searching by detector and source
   ("NaI(Tl) Cs-137 spectrum", "HPGe Eu-152 efficiency calibration spectra") and by
   format ("N42", ".spe", ".chn").
3. Data-availability statements of recent open-access papers on gamma-spectrum analysis
   and radionuclide identification.
4. NIST and national metrology institutes for reference spectra (as opposed to nuclear
   decay data, which is a separate topic handled in Phase 9).
5. Teaching-laboratory repositories of universities that publish raw spectra with their
   lab manuals.

## Rules

- No dataset is downloaded without the acceptance record being started first.
- No analysis result depends on external data unless its provenance record is complete.
- Simulated external data are never reported as experimental validation.
- This task does not block development; an adapter for a new format is written only
  after real files of that format have been inspected.

Sources consulted for the leads above:
[Zenodo 3911859](https://zenodo.org/record/3911859),
[Illinois Data Bank IDB-4860767](https://databank.illinois.edu/datasets/IDB-4860767),
[Dryad 10.7941/D1XC97](https://datadryad.org/dataset/doi:10.7941/D1XC97),
[arXiv 2508.08306](https://arxiv.org/pdf/2508.08306),
[Mendeley Data xmmgbrfgnb](https://data.mendeley.com/datasets/xmmgbrfgnb/1),
[IAEA gamma-ray spectra for testing of spectrum analysis software (journal index)](https://acnpsearch.tweb-dev.unibo.it/singlejournalindex/4827225).
