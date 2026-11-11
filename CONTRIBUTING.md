# Contributing

NSW is at an alpha stage and maintained by one person. Issues and suggestions are
welcome; please open an issue before sending a larger change.

## Ground rules

- **No measurement data in the repository.** Never commit instrument files, databases
  or anything derived from private measurements. Tests use generated or fabricated
  inputs.
- **No nuclear data without provenance.** Reference values need their source, dataset
  identifier, retrieval date and licence, and must be redistributable.
- **No invented scientific values.** Test nuclides are fictitious and named so.
- **Synthetic validation is not experimental validation.** Say which one a result is.
- Scientific packages (`core`, `analysis`, `io`, `detectors`, `simulation`) must not
  import Qt, SQLite or configuration; the interface calls `services` only.

## Before sending a change

```powershell
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe -m ruff format --check src tests scripts
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest
```

A new calculation needs a test against an independent reference (hand calculation,
analytical limit, or a second numerical method) and its formula in
`docs/scientific_methodology.md`. Design choices go into `TECHNICAL_DECISIONS.md`.
