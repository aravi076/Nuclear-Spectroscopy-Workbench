# Post-v1 Roadmap

Prioritised by scientific correctness, then reproducibility, then capability, then
usability. Each item states why it matters, what it needs and how it would be validated.
Nothing here is started automatically; items that need laboratory data or decisions are
marked.

| # | Item | Why | Needs | Validation |
|---|---|---|---|---|
| 1 | Low-energy tail in the HPGe peak model (exponential tail convolved with the Gaussian, optional) | Strong real peaks are reported as poor fits; areas of large peaks may be biased | model, start values, model-selection test against the Gaussian | synthetic peaks with known tail; real laboratory peaks as plausibility check |
| 2 | Decision threshold and detection limit (ISO 11929) for weak peaks and absent lines | Weak-peak reporting and the "missing line" judgement need a standard treatment | background estimate under the peak, uncertainty budget | Monte Carlo false-positive and detection rates |
| 3 | Gain-drift detection and time-dependent energy calibration | One calibration cannot serve all laboratory runs | reference lines in each run; validity period in the calibration record | synthetic drifting spectra; laboratory runs |
| 4 | Fit positions and widths taken from stored calibration and resolution models | Removes manual typing for fixed-shape fits of weak peaks | project store link between models and fits | existing Monte Carlo cases |
| 5 | Energy axis in the interactive plots for verified calibrations | Usability; reports already show it | plot axis transform | GUI tests |
| 6 | Net-area estimate for unfitted library lines | Removes the dependence of "missing" on what the analyst fitted | window and side-band continuum | synthetic spectra with unfitted lines |
| 7 | Save joint gross + background fits in project files | Currently skipped | background reference in the fit result | reopen and reproduce test |
| 8 | Real nuclear data import (DDEP, ENSDF parsers) | Required for real identification | **owner: redistribution terms or user-supplied files** | comparison with the evaluators' tables |
| 9 | True-coincidence-summing and self-attenuation corrections | Close geometry and extended sources | **laboratory: geometry, detector model** | published reference cases |
| 10 | Batch processing and multi-spectrum comparison | Campaigns with many runs | services over project contents | regression tests |
| 11 | Continuous integration (GitHub Actions, tests without the dataset) | Reproducibility for contributors | workflow file in the public repository | green runs |
| 12 | Type checking of tests; Graphify on documents | Engineering hygiene | configuration | CI |

Experimental validation becomes possible only with items supplied by the laboratory:
certificates, geometry, detector model and, for NaI(Tl), measurements.
