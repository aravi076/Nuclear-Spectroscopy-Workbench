"""The nuclear-data model and the reference-data file provider.

All records here are invented. Records named "Xx-999" with evaluation "DDEP" or "ENSDF"
exist only to test how records of two evaluations are kept apart; element "Xx" does not
exist and no value is taken from either evaluation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from nsw.core.nuclear_data import (
    GammaRecord,
    IntensityConvention,
    NuclideRecord,
    Provenance,
    ReferenceLibrary,
    ReuseStatus,
)
from nsw.io import reference_data as rd
from nsw.simulation.reference import FICTITIOUS, fictitious_library

PER_DECAY = IntensityConvention.PER_DECAY


def provenance(evaluation: str = "DDEP", **kw: object) -> Provenance:
    data: dict[str, object] = dict(
        evaluation=evaluation,
        dataset_id=f"invented-{evaluation.lower()}-1",
        citation="invented test record, not nuclear data",
        licence="none (test)",
        reuse_status=ReuseStatus.USER_SUPPLIED,
        retrieval_date=date(2026, 11, 11),
        parser="test/1",
    )
    data.update(kw)
    return Provenance.model_validate(data)


def record(
    evaluation: str = "DDEP", half_life: float | None = 100.0, p: float = 0.5
) -> NuclideRecord:
    return NuclideRecord(
        nuclide="Xx-999",
        provenance=provenance(evaluation),
        half_life_s=half_life,
        lines=(
            GammaRecord(energy_kev=100.0, energy_unc_kev=0.01, intensity=p, convention=PER_DECAY),
            GammaRecord(energy_kev=200.0, intensity=0.2, convention=PER_DECAY),
        ),
    )


def test_relative_intensity_is_never_an_emission_probability() -> None:
    rel = GammaRecord(energy_kev=100.0, intensity=100.0, convention=IntensityConvention.RELATIVE)
    assert not rel.is_absolute
    with pytest.raises(ValueError, match="relative intensity"):
        _ = rel.emission_probability
    absolute = GammaRecord(energy_kev=100.0, intensity=0.85, convention=PER_DECAY)
    assert absolute.emission_probability == 0.85
    # a percentage passed as a probability is caught, not silently accepted
    with pytest.raises(ValidationError, match="percentage"):
        GammaRecord(energy_kev=100.0, intensity=85.0, convention=PER_DECAY)
    for bad in (dict(energy_kev=0.0, intensity=0.5), dict(energy_kev=10.0, intensity=0.0)):
        with pytest.raises(ValidationError):
            GammaRecord(convention=PER_DECAY, **bad)  # type: ignore[arg-type]


def test_record_and_provenance_rules() -> None:
    r = record()
    assert r.key == ("Xx-999", "DDEP") and len(r.absolute_lines) == 2
    assert r.lines[1].energy_unc_kev is None  # missing values stay missing
    assert not r.has_relative_lines and not r.provenance.fictitious
    with pytest.raises(ValidationError, match="not of the form"):
        NuclideRecord(nuclide="cobalt60", provenance=provenance())
    with pytest.raises(ValidationError, match="fictitious"):
        NuclideRecord(nuclide="SYN-X", provenance=provenance())  # fictitious name, real label
    with pytest.raises(ValidationError, match="fictitious"):
        NuclideRecord(nuclide="Xx-999", provenance=FICTITIOUS)
    with pytest.raises(ValidationError, match="must be used together"):
        provenance("FICTITIOUS")
    with pytest.raises(ValidationError, match="SHA-256"):
        provenance(raw_sha256="abc")
    with pytest.raises(ValidationError, match="without a half-life"):
        NuclideRecord(nuclide="Xx-999", provenance=provenance(), half_life_unc_s=1.0)
    line = GammaRecord(energy_kev=100.0, intensity=0.5, convention=PER_DECAY)
    with pytest.raises(ValidationError, match="listed twice"):
        NuclideRecord(nuclide="Xx-999", provenance=provenance(), lines=(line, line))
    with pytest.raises(ValidationError):  # every provenance field is required
        Provenance.model_validate(dict(evaluation="DDEP", dataset_id="x"))


def test_evaluations_are_kept_apart_and_chosen_explicitly() -> None:
    ddep, ensdf = record("DDEP", 100.0, 0.5), record("ENSDF", 101.0, 0.52)
    lib = ReferenceLibrary(name="two", records=(ensdf, ddep))
    assert lib.evaluations == ("DDEP", "ENSDF") and lib.nuclides == ("Xx-999",)
    assert lib.select(("DDEP", "ENSDF")) == (ddep,)
    assert lib.select(("ENSDF", "DDEP")) == (ensdf,)
    assert lib.select(("ENSDF",)) == (ensdf,)
    assert lib.select(("OTHER",)) == ()
    with pytest.raises(ValueError, match="order of preference"):
        lib.select(())
    # the chosen record is one evaluation's record unchanged: nothing is averaged
    assert lib.select(("DDEP",))[0].lines[0].intensity == 0.5
    (conflict,) = lib.conflicts()
    assert conflict.evaluations == ("ENSDF", "DDEP")
    assert any("half-life" in d for d in conflict.differences)
    assert any("emission probability at 100 keV" in d for d in conflict.differences)
    assert not ReferenceLibrary(name="one", records=(ddep,)).conflicts()
    with pytest.raises(ValidationError, match="more than one record"):
        ReferenceLibrary(name="dup", records=(ddep, record("DDEP")))
    with pytest.raises(ValidationError, match="cannot share a library"):
        fictitious_library().merged_with([ddep])


def test_fictitious_library_is_labelled_and_matches_the_generator_sources() -> None:
    lib = fictitious_library()
    assert lib.fictitious and lib.evaluations == ("FICTITIOUS",)
    assert set(lib.nuclides) >= {"SYN-CAL", "SYN-X", "DECOY-A", "DECOY-D"}
    assert all(r.provenance.reuse_status is ReuseStatus.FICTITIOUS for r in lib.records)
    assert all("not nuclear data" in r.provenance.citation for r in lib.records)
    (syn_x,) = lib.records_for("SYN-X")
    assert [(ln.energy_kev, ln.intensity) for ln in syn_x.lines] == [
        (200.0, 0.5),
        (650.0, 0.3),
        (1250.0, 0.2),
    ]
    assert lib.records_for("DECOY-D")[0].has_relative_lines


def test_reference_file_round_trip_keeps_provenance_and_hash(tmp_path: Path) -> None:
    lib = fictitious_library()
    path = rd.write_library(lib, tmp_path / "fictitious.json")
    with pytest.raises(rd.ReferenceDataError, match="could not write"):
        rd.write_library(lib, path)  # existing files are not overwritten
    before = path.read_bytes()
    loaded = rd.load_library("again", [rd.JsonFileProvider(path)])
    assert path.read_bytes() == before  # reading never writes
    digest = hashlib.sha256(before).hexdigest()
    assert len(loaded.records) == len(lib.records)
    for original, read in zip(lib.records, loaded.records, strict=True):
        assert read.provenance.raw_sha256 == digest and read.provenance.parser == rd.PARSER
        assert read.lines == original.lines and read.half_life_s == original.half_life_s
        assert read.provenance.citation == original.provenance.citation
        assert read.provenance.retrieval_date == original.provenance.retrieval_date
    assert "FICTITIOUS" in rd.JsonFileProvider(path).load()[0].provenance.evaluation


def _write(tmp_path: Path, doc: object) -> Path:
    path = tmp_path / "lib.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_malformed_and_unprovenanced_files_are_refused(tmp_path: Path) -> None:
    good = rd.library_document(ReferenceLibrary(name="x", records=(record(),)))
    assert "parser" not in good["records"][0]["provenance"]
    assert rd.JsonFileProvider(_write(tmp_path, good)).load()[0].nuclide == "Xx-999"

    cases: list[tuple[object, str]] = [
        ({"format": "other"}, "not an NSW reference-data file"),
        ({**good, "format_version": 99}, "format version"),
        ({**good, "records": []}, "no records"),
    ]
    no_prov = json.loads(json.dumps(good))
    del no_prov["records"][0]["provenance"]
    cases.append((no_prov, "no provenance"))
    no_licence = json.loads(json.dumps(good))
    del no_licence["records"][0]["provenance"]["licence"]
    cases.append((no_licence, "licence"))
    percent = json.loads(json.dumps(good))
    percent["records"][0]["lines"][0]["intensity"] = 85.1
    cases.append((percent, "percentage"))
    unknown = json.loads(json.dumps(good))
    unknown["records"][0]["lines"][0]["made_up_field"] = 1
    cases.append((unknown, "made_up_field"))
    interface = json.loads(json.dumps(good))
    interface["records"][0]["provenance"]["evaluation"] = "LiveChart"
    cases.append((interface, "interface to ENSDF"))
    for doc, message in cases:
        with pytest.raises(rd.ReferenceDataError, match=message):
            rd.JsonFileProvider(_write(tmp_path, doc)).load()
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe not json")
    with pytest.raises(rd.ReferenceDataError, match="not UTF-8 JSON"):
        rd.JsonFileProvider(bad).load()
    with pytest.raises(rd.ReferenceDataError, match="cannot read"):
        rd.JsonFileProvider(tmp_path / "absent.json").load()
    # the same nuclide twice from one evaluation, in two files
    with pytest.raises(rd.ReferenceDataError, match="more than one record"):
        p = rd.JsonFileProvider(_write(tmp_path, good))
        rd.load_library("dup", [p, p])


def test_known_sources_state_roles_and_unresolved_terms() -> None:
    by_name = {s.evaluation: s for s in rd.KNOWN_SOURCES}
    assert set(by_name) == {"DDEP", "ENSDF"}
    assert by_name["DDEP"].role.startswith("primary")
    assert by_name["ENSDF"].role.startswith("supplementary")
    assert all("checked 2026-11-11" in s.terms for s in rd.KNOWN_SOURCES)
    assert "LIVECHART" in rd.INTERFACES_NOT_EVALUATIONS
