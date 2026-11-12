"""Providers of nuclear reference data.

A *provider* turns one source of decay data into :class:`NuclideRecord` objects of the
source-independent model (``nsw.core.nuclear_data``). The rest of NSW sees only that
model, so a change in a source's file format or web interface is confined to its
provider.

Implemented provider
--------------------
:class:`JsonFileProvider` reads the NSW reference-data file (JSON, format version 1,
documented in docs/science/nuclear_data.md). It is the import mechanism for data the user has
obtained, and the format of the fictitious test library. The file is kept as the raw
source: its SHA-256 is stored in every record read from it.

Sources without a provider
--------------------------
``KNOWN_SOURCES`` lists the evaluations NSW is designed for (DDEP primary, ENSDF
supplementary) with the state of their reuse terms. Neither has a parser yet: no real
file of either has been inspected and their redistribution terms are not established
(docs/science/nuclear_data.md). No values of either are contained in NSW.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from nsw.core.errors import ProcessingError
from nsw.core.nuclear_data import NuclideRecord, ReferenceLibrary

FORMAT_NAME = "nsw-reference-data"
FORMAT_VERSION = 1
PARSER = f"nsw.io.reference_data.JsonFileProvider/{FORMAT_VERSION}"
#: interfaces to ENSDF: not evaluations of their own, never a record's ``evaluation``
INTERFACES_NOT_EVALUATIONS = {"IAEA", "LIVECHART", "IAEA LIVECHART", "NUDAT", "NUDAT3"}


class ReferenceDataError(ProcessingError):
    """A reference-data file is malformed or violates the provenance rules."""


@dataclass(frozen=True, slots=True)
class KnownSource:
    evaluation: str
    role: str
    maintainer: str
    url: str
    terms: str  # what is established about reuse, with the date it was checked
    provider: str  # implementation state


KNOWN_SOURCES: tuple[KnownSource, ...] = (
    KnownSource(
        evaluation="DDEP",
        role="primary: recommended decay data (energies, emission probabilities, half-lives)",
        maintainer="Decay Data Evaluation Project, hosted by LNE-LNHB (CEA)",
        url="https://www.lnhb.fr/home/nuclear-data/",
        terms=(
            "checked 2026-11-11: the site asks for citation of the evaluations (BIPM-5 "
            "monographs / Metrologia) and states no licence or redistribution terms; "
            "bundling with NSW is not established"
        ),
        provider="none: import through an NSW reference-data file supplied by the user",
    ),
    KnownSource(
        evaluation="ENSDF",
        role="supplementary: used only where DDEP has no suitable evaluation, as a "
        "separately identified source",
        maintainer="International Network of Nuclear Structure and Decay Data Evaluators; "
        "database maintained by NNDC, Brookhaven National Laboratory",
        url="https://www.nndc.bnl.gov/ensdf/",
        terms=(
            "checked 2026-11-11: citation guidelines published (cite the Nuclear Data "
            "Sheets evaluation, or the database with the date consulted); no explicit "
            "redistribution licence found; the JSON API is a public beta"
        ),
        provider="none: import through an NSW reference-data file supplied by the user",
    ),
)


class ReferenceDataProvider(Protocol):
    """One source of nuclide records."""

    @property
    def description(self) -> str: ...

    def load(self) -> tuple[NuclideRecord, ...]: ...


def _check_evaluation_name(record: NuclideRecord) -> None:
    name = record.provenance.evaluation.strip().upper()
    if name in INTERFACES_NOT_EVALUATIONS:
        raise ReferenceDataError(
            f"{record.nuclide}: {record.provenance.evaluation!r} is an interface to ENSDF "
            "data, not an evaluation. Record the evaluation as 'ENSDF' and name the "
            "interface in 'accessed_through'"
        )


class JsonFileProvider:
    """Reads an NSW reference-data file. Never writes to it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    def description(self) -> str:
        return f"NSW reference-data file {self.path.name}"

    def load(self) -> tuple[NuclideRecord, ...]:
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise ReferenceDataError(f"cannot read {self.path}: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReferenceDataError(f"{self.path.name} is not UTF-8 JSON: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("format") != FORMAT_NAME:
            raise ReferenceDataError(f"{self.path.name} is not an NSW reference-data file")
        if doc.get("format_version") != FORMAT_VERSION:
            raise ReferenceDataError(
                f"{self.path.name}: format version {doc.get('format_version')!r} is not "
                f"supported (this NSW reads version {FORMAT_VERSION})"
            )
        entries = doc.get("records")
        if not isinstance(entries, list) or not entries:
            raise ReferenceDataError(f"{self.path.name} contains no records")
        records: list[NuclideRecord] = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("provenance"), dict):
                raise ReferenceDataError(
                    f"{self.path.name}: record {i + 1} has no provenance; every record "
                    "needs its source, dataset, citation, licence and retrieval date"
                )
            data: dict[str, Any] = dict(entry)
            data["provenance"] = {**entry["provenance"], "parser": PARSER, "raw_sha256": digest}
            try:
                record = NuclideRecord.model_validate(data)
            except ValidationError as exc:
                name = entry.get("nuclide", f"record {i + 1}")
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                )
                raise ReferenceDataError(f"{self.path.name}, {name}: {problems}") from exc
            _check_evaluation_name(record)
            records.append(record)
        return tuple(records)


def load_library(name: str, providers: list[ReferenceDataProvider]) -> ReferenceLibrary:
    """All records of ``providers`` in one library. Two records for the same nuclide from
    the same evaluation are an error; from different evaluations they are kept side by
    side (see :meth:`ReferenceLibrary.select`)."""
    records: list[NuclideRecord] = []
    for provider in providers:
        loaded = provider.load()
        for record in loaded:
            _check_evaluation_name(record)
        records.extend(loaded)
    try:
        return ReferenceLibrary(name=name, records=tuple(records))
    except ValidationError as exc:
        raise ReferenceDataError("; ".join(e["msg"] for e in exc.errors())) from exc


def library_document(library: ReferenceLibrary) -> dict[str, Any]:
    """The JSON document of a library (``parser`` and ``raw_sha256`` are set on reading,
    so they are not written)."""
    records = []
    for record in library.records:
        data = record.model_dump(mode="json", exclude_none=True)
        data["provenance"].pop("parser", None)
        data["provenance"].pop("raw_sha256", None)
        records.append(data)
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "library": library.name,
        "records": records,
    }


def write_library(library: ReferenceLibrary, path: Path, overwrite: bool = False) -> Path:
    """Write a library as an NSW reference-data file. Existing files are kept unless
    ``overwrite`` is set."""
    text = json.dumps(library_document(library), indent=2, ensure_ascii=False) + "\n"
    try:
        with Path(path).open("w" if overwrite else "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        raise ReferenceDataError(f"could not write {path}: {exc}") from exc
    return Path(path)
