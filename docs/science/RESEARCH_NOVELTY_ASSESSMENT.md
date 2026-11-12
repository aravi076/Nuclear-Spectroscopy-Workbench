# Research Novelty Assessment

Date: 2026-11-12. Purpose: an honest comparison of NSW with existing gamma-spectrum
analysis software and literature, to find what NSW could defensibly contribute and what
evidence would be needed. Competitor capabilities are taken only from the cited vendor
documents or publications; where only third-party copies of manuals were found, this is
said. Nothing here claims that NSW cannot be reproduced.

## 1. Existing tools and their established capabilities

| Tool | Type | Documented capabilities relevant to NSW | Source |
|---|---|---|---|
| ORTEC GammaVision (v8) | commercial | Mariscotti peak search with ROIs of 3 × FWHM; ROI reports with gross/net areas; analysis engines for complex HPGe spectra, small nuclide mixes and NaI spectra; user nuclide libraries with key-line and MDA criteria; 19 MDA methods; multiplet deconvolution; decay, summing and absorption corrections; N42.42 files | ORTEC product page https://www.ortec-online.com/products/software/gammavision; V8 user manual (third-party copy, https://usermanual.wiki/Document/GammaVision20V820Users20Manual.1982380323/html) |
| ORTEC LabVision | commercial | GammaVision engines plus newer peak search/fitting and a Monte Carlo efficiency-transfer tool | brochure https://www.htds.fr/wp-content/uploads/2026/05/LVIS_UK.pdf |
| Mirion Genie 2000 / Genie 4 | commercial | interactive spectral analysis, efficiency correction, nuclide identification using all library lines with branching ratios and half-lives; Interactive Peak Fit for reviewing automatic results with residuals in σ units; interactive NID and peak labelling; batch and programming interfaces | spec sheets https://assets-mirion.mirion.com/prod-20220822/cms4_mirion/files/pdf/spec-sheets/c47691_genie-2000_gamma_analysis_software_spec_sheet_1.pdf, https://www.mirion.com/products/genie-2000-interactive-peak-fit |
| Hypermet-PC | commercial (research origin) | HPGe peak shape with tails and step (Hypermet function), spectrum fitting | https://iki.kfki.hu/nuclear/hypc/index_en.shtml |
| InterSpec (Sandia) | open source | peak-based analysis, nuclide identification, activity and shielding fits, reference photopeak lines, relative-efficiency isotopics, many file formats, desktop/web/mobile | https://github.com/sandialabs/Interspec |
| Python tools (NASA-gamma, SAPPY, LuRaCs, gammaspotter) | open source / research | peak finding and fitting, calibration, peak identification against databases; SAPPY: shared nuclear data for HPGe and microcalorimeter spectra | PyPI and INMM pages found 2026-11-12 (e.g. https://pypi.org/project/luracs/, https://resources.inmm.org/node/73273) |
| InterWinner, PyMca | — | not verified in this assessment (no primary documentation retrieved); PyMca is principally an X-ray fluorescence tool | to do |

Not verified: GammaVision, Genie and InterSpec details beyond the sources above;
algorithm internals of commercial tools are not public.

## 2. What the literature says about analysis quality

- **1995 IAEA intercomparison** (Blaauw et al., *Nucl. Instrum. Methods A* 387 (1997)
  416–432): 12 PC programs on IAEA test spectra; areas without relative bias and
  singlets in statistical control, but **doublet areas lacked statistical control** and
  **peak-position uncertainties were too small or missing**.
- **Nielsen & Pålsson** (1998; listed in the same journal volume by one index and
  inconsistently by others — to be confirmed before citing): only 4 of 15 submitted data sets had acceptable
  precision and accuracy for isolated peaks; user influence was significant; more
  sophisticated fitting was not more accurate.
- **2002 IAEA low-level test spectra** (Arnold et al., 2005): comparison of eight
  packages including GammaVision, Genie 2000, Hyperlab and InterWinner against
  certified activities (numerical outcomes not retrieved).
- The test spectra themselves: "IAEA γ-ray spectra for testing of spectrum analysis
  software", *NIM A* 387 (1997) 410–415.

These findings identify where a new tool could add measurable value: **statistical
control of uncertainties (especially doublets and positions)** and **less dependence
on the user**.

## 3. Overlap with NSW

Interactive ROIs, automatic peak search, multiplet fitting, library-based
identification, efficiency and activity, MDA/detection limits, and reference-line
display all exist in commercial and open tools. NSW's interactive workflow (planned in
`docs/design/UX_REDESIGN_PLAN.md`) is therefore **not novel in itself**; it is necessary for
usability.

## 4. Potential differences and contributions

| # | Candidate contribution | Why it may matter | Evidence NSW has | Evidence missing |
|---|---|---|---|---|
| C1 | **Uncertainty calibration as a published, tested property**: Monte Carlo coverage of areas, positions, widths, calibrations, efficiencies and activities, run in CI with pass criteria | addresses exactly the deficits found in the 1995 intercomparison (positions, doublets) | coverage tests for singlets, joint fits, calibration, activity chain (`VALIDATION_REPORT.md`) | doublet coverage across separations; position-uncertainty coverage on IAEA test spectra |
| C2 | **Low-count goodness-of-fit** with the exact Poisson deviance moments instead of χ² tables | fit acceptance at low counts is otherwise misleading | implemented and validated on synthetic data | comparison of fit-acceptance rates with other tools |
| C3 | **Joint gross + background profile-likelihood fit** (no subtraction, no clipping) | avoids subtraction artefacts and negative-count handling for low-level spectra | implemented, validated | benchmark against subtract-then-fit on IAEA low-level spectra |
| C4 | **Evidence classes for nuclide candidates with measured false-identification rates** (R11) and uncertainty-aware matching | commercial NID is documented as tolerance-based; rates are rarely published | decoy-library validation (0 decoys ≥ probable in 250 trials) | calibrated rates across libraries, count levels and interferences |
| C5 | **End-to-end reproducibility**: every stored result recomputed on reopening, byte-identical reports, provenance with analyst decisions | auditability; supports the user-influence problem by recording decisions | fits and calibrations recomputed today; deterministic reports | provenance engine (`docs/design/SCIENTIFIC_PROVENANCE_DESIGN.md`); a reproduction study by a second analyst |
| C6 | **Provenance-preserving nuclear data** (evaluation never mixed; intensity convention enforced) | prevents silent mixing of DDEP and ENSDF values | data model and checks | real libraries (terms pending) |
| C7 | **Open, tested limits** following ISO 11929 with the fit-based ũ(ỹ) | transparency of MDA-type numbers | design only | implementation and comparison with UncertRadio |

## 5. Benchmarking methods

1. **IAEA test spectra** (if obtainable under usable terms; see
   `docs/science/external_dataset_discovery.md`): peak areas and uncertainties against the
   reference values, scored as in the 1995 intercomparison; report pulls, not only
   differences.
2. **Synthetic benchmark suite** with known truth (already in the validation runner),
   published with seeds so others can run their tools on the same spectra.
3. **Analyst-dependence study**: the same spectra analysed by several users with and
   without the guided workflow; spread of results.
4. **Identification**: decoy-library and interference scenarios; false-identification
   and miss rates per evidence class.
5. **Comparison with InterSpec** (open source) on shared synthetic spectra, since its
   results can be generated without licences.

## 6. Publication opportunities (realistic)

- A software/methods paper on statistical control of peak-area and position
  uncertainties with open benchmarks (C1–C3) — e.g. *Applied Radiation and Isotopes* or
  *Journal of Radioanalytical and Nuclear Chemistry*; requires the benchmark results
  first.
- A short paper or conference contribution on calibrated nuclide-candidate evidence
  (C4).
- A software paper (e.g. *Journal of Open Source Software*) once the public repository
  has CI, documentation and a release.

## 7. What must not be claimed

- That NSW's interactive features are unique.
- That NSW is more accurate than commercial software, without the benchmarks above.
- ISO 11929 conformity (see `docs/design/ISO_11929_DETECTION_LIMIT_DESIGN.md`).
- Any experimental validation from synthetic studies.

## 8. Next steps

1. Retrieve primary documentation for InterWinner and current Genie 4 algorithms.
2. Ask the IAEA for the test spectra and their terms of use.
3. Implement the provenance engine and R11 study (prerequisites for C4, C5).
