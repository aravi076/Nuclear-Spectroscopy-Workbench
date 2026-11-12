# External Dataset Survey

Status: survey of 2026-11-12 (Phase 12), extending the leads of 2026-11-10.
**Nothing has been downloaded, imported or relied upon.** Each entry records what the
official page states and what remains unverified. Several official pages refused
automated access (HTTP 402/403); for those the information comes from search-engine
summaries of the page and is marked *(not read directly)*. Every such entry must be
checked in a browser before use.

## Why external data

1. **No experimental NaI(Tl) spectra exist in NSW.** NaI(Tl) support is tested on
   simulations only.
2. HPGe spectra with documented sources, geometry and reference values would test peak
   areas, efficiency and activity against independent results; the laboratory dataset
   lacks certificates and geometry.
3. New file formats justify new import adapters only after real files are inspected.

## Acceptance criteria

A dataset may be used only when `data/reference/external/<name>/PROVENANCE.md` (outside
Git) records: persistent identifier and repository; authors and publication; a licence
that permits the use and its citation text; detector type and specification;
**measured or simulated** (simulated data stay in the synthetic category); file format
verified against a second reader or a published specification; acquisition metadata
(live/real time, date, source identity) and, for quantitative use, activity, reference
date, uncertainty and geometry; retrieval date and SHA-256 of every file. External data
are never committed and are labelled by origin in the application.

## Candidates

| Dataset | Official source | Detector | Measured / simulated | Format | Source and geometry metadata | Calibration information | Licence and redistribution | Possible benchmarking role |
|---|---|---|---|---|---|---|---|---|
| IAEA International Database of Reference Gamma Spectra (IDB), described in *Scientific Data* (2025): 551 spectra of U, Pu and MOX materials, grouped by quality, material, detector and gain | [nds.iaea.org/idb](https://nds.iaea.org/idb); paper [PMC12660317](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12660317/) | several; LaBr₃(Ce) and CZT confirmed by a JRC record; HPGe and NaI(Tl) coverage *not confirmed* | measured | not verified; metadata queryable, programmatic API stated | source information, measurement configuration and detector specification queryable *(not read directly)* | not verified | site states CC BY 4.0 for data and metadata unless stated otherwise, and asks to cite the database and data authors *(not read directly)*; an earlier version required registration | Most promising: real spectra with documented detectors under an open licence. Uses: peak search and fitting on real line shapes (tails, multiplets of U/Pu X-ray and gamma regions), format adapters. Not suitable for efficiency or activity (no calibration sources). |
| IAEA test spectra for intercomparison of gamma-spectrometry software (1995/1998 set built from a Ra-226 spectrum; 2002 low-level set with Marinelli and pillbox geometries, e.g. MARITSMALL, PITBIG, MIX1EQ, MIX1NEQ) | publications: NIM A 2005 (DOI 10.1016/j.nima.2004.10.012) and earlier; no current download page found | HPGe | measured, with reference values | not verified | geometry and certified sources for the 2002 set *(per publications)* | calibration spectra included *(per publications)* | not established; obtainable only by request to the IAEA or the authors | The recognised benchmark for peak areas, uncertainties and activities. Highest value for NSW if a copy and permission can be obtained. |
| Timestamped list-mode data from coincidence γ-ray spectrometry with HPGe detectors on air-filter samples, *Data in Brief* (2025) | [PubMed 40677253](https://pubmed.ncbi.nlm.nih.gov/40677253/) | HPGe array | measured | list mode (new format) | air-filter samples | not verified | not verified (journal is open access; data licence not read) | List-mode handling and coincidence studies; outside NSW's current scope. |
| Example gamma spec peaks, Zenodo 10.5281/zenodo.3911859 (2020), T. Stainer | [zenodo.org/record/3911859](https://zenodo.org/record/3911859) | germanium ("HGe"); model not stated | both: real and Geant4, in separate files | CSV, rows of 13 bins | none: "sources and energies are hidden" | none | **CC BY 4.0 (read directly)** | Peak/no-peak classification snippets only; not full spectra. Limited role (detection tests on real noise); no physics reference values. |
| Gamma-ray spectra templates for source interdiction and uranium enrichment, Illinois Data Bank IDB-4860767 | [databank.illinois.edu/datasets/IDB-4860767](https://databank.illinois.edu/datasets/IDB-4860767) | NaI(Tl) (tagged) | not established | not verified | not verified | not verified | CC BY *(not read directly)* | Possible NaI(Tl) line-shape data, if measured. Built for machine learning. |
| Synthetic urban gamma-ray spectra (RADAI), Dryad 10.7941/D1XC97 (2023) | [datadryad.org](https://datadryad.org/dataset/doi:10.7941/D1XC97) | simulated 2×4×16-inch NaI(Tl) | **simulated** | HDF5, 2.32 GB | simulated urban scenes | — | Dryad states content is licensed for reuse (no specific licence named on the page) | Synthetic category only: large-scale NaI(Tl) detection tests. |
| Open benchmark for γ-spectrometry analysis, arXiv 2508.08306 (2025) | [arxiv.org/abs/2508.08306](https://arxiv.org/abs/2508.08306) | simulated 3×3-inch NaI(Tl) | **simulated** (experimental background) | not verified | nine radionuclides | — | not verified | Synthetic category only: identification benchmark. |
| INL Gamma-Ray Spectrum Catalogue (Ge and NaI(Tl) editions, after R. L. Heath) | [gammaray.inl.gov](https://gammaray.inl.gov/SitePages/catalog_ge.aspx) | HPGe and NaI(Tl) | measured | catalogue plots; availability of digital spectra not verified | per-nuclide source spectra *(per catalogue description)* | not verified | not verified (pages refused automated access) | Visual reference for line shapes and Compton features; quantitative use only if digital spectra and terms are confirmed. |
| Mendeley Data ftcsyc4ygr | [data.mendeley.com/datasets/ftcsyc4ygr](https://data.mendeley.com/datasets/ftcsyc4ygr) | portable internal-contamination monitor | measured *(per search summary)* | not verified | Cs-137, Co-60 | not verified | licence not confirmed | Low: not a laboratory spectrometer. |
| Mendeley Data xmmgbrfgnb | Mendeley Data | LaBr₃(Ce) | simulated | — | — | — | — | Out of scope. |

## Conclusions

- **No measured NaI(Tl) dataset of standard sources with documented acquisition
  conditions and a confirmed open licence was found.** NaI(Tl) remains validated on
  simulations only, and the README and reports say so.
- The **IAEA IDB** is the best open candidate for real HPGe-type spectra, but it serves
  peak analysis and format testing, not efficiency or activity, and its detector
  coverage and file formats must be confirmed first.
- The **IAEA 2002 software-intercomparison spectra** remain the most valuable
  benchmark for peak areas and activities; they are not publicly downloadable and must
  be requested.
- Simulated collections (RADAI, arXiv 2508.08306) can extend the synthetic tests but
  never count as experimental validation.

## Next steps (need the project owner)

1. Decide whether to request the IAEA intercomparison spectra (IAEA Nuclear Data
   Section or the authors) and whether the laboratory already holds a copy.
2. Open the IAEA IDB in a browser, confirm the licence statement, detector types and
   formats, and start a provenance record for a small subset of HPGe spectra.
3. Measured NaI(Tl) spectra are most simply obtained in the laboratory: standard
   sources at a recorded geometry with certificates.

## Rules

- No dataset is downloaded before its provenance record is started.
- No result depends on external data unless the provenance record is complete.
- Simulated external data are never reported as experimental validation.
- An import adapter for a new format is written only after real files are inspected.

Sources consulted: the pages linked in the table, and
[IAEA IDB](https://www-nds.iaea.org/idb),
[INMM abstract on the IDB](https://resources.inmm.org/node/17224),
[JRC record JRC98340](https://publications.jrc.ec.europa.eu/repository/handle/JRC98340),
[2002 IAEA software intercomparison (CROSBI 114339)](https://www.bib.irb.hr/197730),
[KINS paper on HyperGam with the 2002 set](https://kns.org/files/pre_paper/7/398박병건.pdf).
