# Data in This Repository

## What is included

| Data | Origin | Terms |
|---|---|---|
| Synthetic spectra (`scripts/generate_synthetic.py`, written to `data/synthetic/`, git-ignored) | produced by NSW's own generator from invented parameters | part of NSW; MIT, as the code |
| Fictitious sources and reference library (`nsw.simulation.sources`, `nsw.simulation.reference`: SYN-CAL, SYN-X, DECOY-…) | invented values, labelled as such; **not nuclear data** | part of NSW; MIT, as the code |
| Fabricated format fixtures (`tests/fixtures/builders.py`) | byte sequences built by the tests in the instrument file formats, with invented counts | part of NSW; MIT, as the code |
| Screenshots (`docs/images/`) | the application showing synthetic spectra | part of NSW; MIT, as the code |

## What is not included

- **Laboratory measurements.** The HPGe measurements used privately during development
  belong to their laboratory. They are not in this repository, are not covered by its
  licence, and are not described here.
- **Nuclear decay data.** No energies, emission probabilities or half-lives of real
  nuclides are included. NSW is designed for DDEP (primary) and ENSDF (supplementary)
  data, which users obtain themselves and import with their provenance.

## Redistribution status of the nuclear-data sources (checked 2026-11-11)

| Source | Stated on the official site | Redistribution |
|---|---|---|
| DDEP, hosted by LNE-LNHB (https://www.lnhb.fr/home/nuclear-data/) | request to cite the evaluations | no licence or redistribution terms found: **not bundled** |
| ENSDF, maintained by NNDC (https://www.nndc.bnl.gov/ensdf/) | citation guidelines | no explicit redistribution licence found: **not bundled** |

Data a user imports keep the terms of their source; NSW records those terms with every
record (`docs/nuclear_data.md`).

## Public datasets

`docs/external_dataset_discovery.md` lists candidate public spectra for future
benchmarking. None has been downloaded or included; each has its own licence, to be
checked before use.
