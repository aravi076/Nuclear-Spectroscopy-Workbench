# Nuclear Reference Data

NSW contains **no nuclear decay data**. This page states which sources it is designed
for, what is known about their reuse terms, and how data are imported.

## Strategy (decided by the project owner, 2026-11-11)

| Evaluation | Role | Official source |
|---|---|---|
| DDEP (Decay Data Evaluation Project), hosted by LNE-LNHB | primary: gamma energies, emission probabilities, half-lives | https://www.lnhb.fr/home/nuclear-data/ |
| ENSDF, maintained by NNDC | supplementary, where DDEP has no suitable evaluation; always identified separately | https://www.nndc.bnl.gov/ensdf/ |

The IAEA LiveChart and NuDat are interfaces to ENSDF, not evaluations of their own. A
record obtained through them is an ENSDF record (`evaluation: "ENSDF"`,
`accessed_through: "IAEA LiveChart"`); the importer refuses them as evaluation names.

Values of different evaluations are never combined. A library may hold a DDEP and an
ENSDF record for the same nuclide; an analysis uses one of them, chosen by an explicit
order of preference (default DDEP, then ENSDF), and reports which.

## Reuse terms (checked 2026-11-11)

| Source | Found | Not found | Consequence |
|---|---|---|---|
| DDEP / LNHB | request to cite the evaluations (BIPM-5 monographs, Metrologia), with BibTeX per volume | any licence, terms of use or statement on redistribution, on the nuclear-data pages or a legal notice | not bundled; open question to the owner / LNHB |
| ENSDF / NNDC | citation guidelines: cite the Nuclear Data Sheets evaluation (10 or fewer nuclides) or the database with the date consulted; JSON API in public beta, release date published | an explicit redistribution licence | not bundled; API not used |

Because redistribution rights are not established, NSW neither downloads nor ships
either dataset. No parser for DDEP or ENSDF files exists yet: no real file of either
has been inspected. The user imports data they have obtained themselves, in the format
below. This is recorded in `nsw.io.reference_data.KNOWN_SOURCES`.

## Record model (`nsw.core.nuclear_data`)

Per nuclide and evaluation: nuclide, half-life with uncertainty and the original
wording, decay branches (mode, daughter, fraction), photon lines, comment, uninterpreted
source fields. Per line: energy and uncertainty (keV), intensity and uncertainty,
**intensity convention**, origin (gamma, X-ray, annihilation), transition identity.
Per record a provenance: evaluation, dataset identifier, evaluation date, retrieval
date, citation, licence text, reuse status, URL, interface used, parser and version,
SHA-256 of the file read, transformations applied.

Rules enforced by the model:

- `photons-per-decay` is the only convention that is an emission probability. A
  `relative` intensity is accepted for energy matching but never used as a probability;
  a per-decay value above 2 is refused (a percentage or a relative intensity).
- A value the source does not give stays empty.
- Fictitious test records (names `SYN-…`, `DECOY-…`, evaluation `FICTITIOUS`) cannot
  share a library with real data, and are refused for measured spectra.

## Import file (format version 1)

UTF-8 JSON, read by `nsw.services.identification.load_reference_file`:

```json
{
  "format": "nsw-reference-data",
  "format_version": 1,
  "library": "name of the library",
  "records": [
    {
      "nuclide": "<Symbol-A, e.g. Xx-123>",
      "half_life_s": 0.0,
      "half_life_unc_s": 0.0,
      "half_life_as_stated": "<value and unit as printed by the source>",
      "provenance": {
        "evaluation": "DDEP",
        "dataset_id": "<identifier used by the source>",
        "evaluation_date": "<as given by the source>",
        "retrieval_date": "2026-11-11",
        "citation": "<citation requested by the source>",
        "licence": "<terms as stated by the source, or 'none stated'>",
        "reuse_status": "user-supplied",
        "url": "<where it was obtained>"
      },
      "lines": [
        {
          "energy_kev": 0.0,
          "energy_unc_kev": 0.0,
          "intensity": 0.0,
          "intensity_unc": 0.0,
          "convention": "photons-per-decay"
        }
      ]
    }
  ]
}
```

The zeros are placeholders, not data. Every provenance field shown is required except
`evaluation_date` and `url`. The file is never modified by NSW; its SHA-256 is stored
with every record read from it. Unknown fields are refused, so a typing error cannot
silently drop a value.

## Open question for the project owner

Whether DDEP and ENSDF data may be redistributed with NSW (in the repository or the
installer). Until that is answered in writing by the sources or their published terms,
the import file is the only route.
