"""The SQLite project store and the project service (synthetic and fabricated data)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from nsw.analysis.fitting import fit_peaks_joint
from nsw.analysis.peaks import PeakSearchSettings, find_peak_candidates
from nsw.analysis.resolution import ResolutionVariable, WidthPoint, fit_resolution
from nsw.core.spectrum import SpectrumKind
from nsw.database.store import ProjectStore, ProjectStoreError
from nsw.services import calibration as cs
from nsw.services import identification as ids
from nsw.services import project as ps
from nsw.simulation import presets, sources
from nsw.simulation.generator import generate
from tests.conftest import make_raw
from tests.unit.test_identification_service import spectrum_and_fits


def new_store(tmp_path: Path, name: str = "demo") -> ProjectStore:
    return ps.create_project(tmp_path / name, "Demo project")


def test_create_open_and_refuse_foreign_or_existing_files(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert store.path.suffix == ".nswproj"
    info = store.info
    assert info["name"] == "Demo project" and info["schema_version"] == 1
    store.close()
    with pytest.raises(ProjectStoreError, match="already exists"):
        ps.create_project(tmp_path / "demo.nswproj", "again")
    with ps.open_project(tmp_path / "demo.nswproj") as reopened:
        assert [a for _, a, _ in reopened.audit_log()] == ["create", "open"]
    foreign = tmp_path / "other.db"
    sqlite3.connect(foreign).execute("CREATE TABLE x (y)").connection.close()
    with pytest.raises(ProjectStoreError, match="not an NSW project"):
        ProjectStore.open(foreign)
    text = tmp_path / "text.nswproj"
    text.write_text("hello", encoding="utf-8")
    with pytest.raises(ProjectStoreError, match="not an NSW project"):
        ProjectStore.open(text)
    with pytest.raises(ProjectStoreError, match="no project file"):
        ProjectStore.open(tmp_path / "absent.nswproj")
    with pytest.raises(ProjectStoreError, match="needs a name"):
        ProjectStore.create(tmp_path / "x.nswproj", " ", "0")


def test_project_is_never_written_into_a_protected_folder(tmp_path: Path) -> None:
    protected = tmp_path / "dataset"
    protected.mkdir()
    with pytest.raises(ProjectStoreError, match="read-only experimental dataset"):
        ps.create_project(protected / "p", "x", protected_roots=[protected])
    assert not any(protected.iterdir())


def test_newer_schema_is_refused_unchanged(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    path = store.path
    store._db.execute("INSERT INTO schema_version VALUES (99, 'future')")
    store._db.commit()
    store.close()
    before = path.read_bytes()
    with pytest.raises(ProjectStoreError, match="newer NSW"):
        ProjectStore.open(path)
    assert path.read_bytes() == before


def test_spectra_round_trip_bit_for_bit(tmp_path: Path) -> None:
    spectrum, _ = spectrum_and_fits(sources.SYN_X)
    measured = make_raw(np.arange(200) % 17, start_time=datetime(2026, 4, 27, 10, 0, 0))
    with new_store(tmp_path) as store:
        assert ps.save_spectrum(store, spectrum) is True
        assert ps.save_spectrum(store, spectrum) is False  # not stored twice
        ps.save_spectrum(store, measured)
        for original in (spectrum, measured):
            back = store.spectrum(original.data_sha256)
            assert back.data_sha256 == original.data_sha256
            assert np.array_equal(back.counts, original.counts)
            assert back.origin is original.origin and back.kind is original.kind
            assert back.start_time == original.start_time
            assert back.source == original.source
            assert back.energy_calibration == original.energy_calibration
            assert back.provenance == original.provenance
        assert next(n for _, n, _, _ in store.spectrum_names()).startswith("ident-")


def test_damaged_counts_are_detected(tmp_path: Path) -> None:
    spectrum, _ = spectrum_and_fits(sources.SYN_X)
    store = new_store(tmp_path)
    ps.save_spectrum(store, spectrum)
    store.close()
    # tamper with the file below the store (triggers prevent it through SQL updates)
    db = sqlite3.connect(tmp_path / "demo.nswproj")
    db.execute("DROP TRIGGER measurement_no_update")
    other = generate(presets.hpge_scenarios()[0]).spectrum
    from nsw.database.store import _pack

    db.execute("UPDATE measurement SET counts = ?", (_pack(other.counts),))
    db.commit()
    db.close()
    with (
        ProjectStore.open(tmp_path / "demo.nswproj") as store,
        pytest.raises(ProjectStoreError, match="damaged"),
    ):
        store.spectrum(spectrum.data_sha256)


def test_results_are_append_only(tmp_path: Path) -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    with new_store(tmp_path) as store:
        ps.save_fits(store, spectrum, fits)
        for sql in (
            "UPDATE analysis_run SET result = '{}'",
            "DELETE FROM analysis_run",
            "DELETE FROM measurement",
            "UPDATE audit_log SET action = 'x'",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                store._db.execute(sql)
        assert len(store.runs("peak-fit")) == 3


def test_full_project_reopens_with_reproduced_fits(tmp_path: Path) -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_CAL, live=20000.0, seed=11)
    background = generate(presets.hpge_scenarios()[-1]).spectrum
    search = find_peak_candidates(spectrum, PeakSearchSettings(fwhm_channels=(4.0, 8.0)))
    rows = cs.peak_rows(fits)
    points = [
        cs.make_point(r, ln.energy_kev, 0.01, "synthetic ground truth")
        for r, ln in zip(rows, sources.SYN_CAL.lines, strict=True)
    ]
    cal_fit = cs.calibrate(points, 1)
    record = cs.make_record(spectrum, cal_fit, datetime(2026, 11, 12))
    resolution = fit_resolution(
        [WidthPoint(r.centroid, r.fwhm, r.fwhm_unc) for r in rows], ResolutionVariable.CHANNEL
    )
    calibrated = cs.apply_calibration(spectrum, record)
    library = ids.fictitious_library()
    identification = ids.identify(calibrated, fits, library)
    with new_store(tmp_path) as store:
        ps.save_peak_search(store, spectrum, search)
        ps.save_fits(store, spectrum, fits)
        ps.save_calibration(store, spectrum, record, {"chi2": cal_fit.chi2})
        ps.save_resolution(store, spectrum, resolution)
        ps.save_reference_library(store, library)
        ps.save_identification(store, calibrated, identification, library.name)
        ps.save_spectrum(store, background)
        store.set_background(background.data_sha256, True, "designated in test")
        ps.save_record(store, "validation-run", None, {"trials": 40}, {"failed": 0},
                       synthetic=True)  # fmt: skip
        backup = store.backup()
    assert backup.exists() and backup.name.startswith("demo.backup.")

    with ps.open_project(tmp_path / "demo.nswproj") as store:
        contents = ps.load_project(store)
    assert contents.problems == []
    assert set(contents.spectra) == {spectrum.data_sha256, background.data_sha256}
    assert contents.spectra[background.data_sha256].kind is SpectrumKind.BACKGROUND
    assert contents.backgrounds == {background.data_sha256: "designated in test"}
    restored = contents.fits[spectrum.data_sha256]
    assert len(restored) == len(fits)
    for old, new in zip(fits, restored, strict=True):
        assert new.peaks[0].area == pytest.approx(old.peaks[0].area, rel=1e-6)
    ((sha, cal),) = contents.calibrations
    assert sha == spectrum.data_sha256 and cal == record and cal.is_verified
    kinds = [r.kind for r in contents.history]
    assert kinds.count("peak-fit") == 8 and "identification" in kinds
    assert "validation-run" in kinds and "peak-search" in kinds
    headers, table = ps.history_table(contents.history, contents.names)
    assert headers[2] == "Analysis" and any("SYN-CAL" in row[5] for row in table)
    with ProjectStore.open(backup) as copy:
        assert len(copy.runs()) == len(contents.history)
        (_, _, evaluations, fictitious, document, _) = copy.reference_datasets()[0]
        assert fictitious and evaluations == ["FICTITIOUS"] and document["records"]


def test_joint_fit_needs_its_background_and_reproduces(tmp_path: Path) -> None:
    gross = generate(presets.hpge_scenarios()[1]).spectrum
    background = generate(presets.hpge_scenarios()[-1]).spectrum
    fit = fit_peaks_joint(gross, background, 385, 411, [398.0], 4.0)
    with new_store(tmp_path) as store:
        with pytest.raises(ValueError, match="background spectrum"):
            ps.save_fits(store, gross, [fit])
        ps.save_fits(store, gross, [fit], background=background)
        contents = ps.load_project(store)
    assert contents.problems == []
    assert contents.fits[gross.data_sha256][0].method == "joint-poisson"


def test_a_fit_that_does_not_reproduce_is_reported(tmp_path: Path) -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    with new_store(tmp_path) as store:
        ps.save_fits(store, spectrum, fits[:1])
        store._db.execute("DROP TRIGGER analysis_run_no_update")
        store._db.execute(
            "UPDATE analysis_run SET result = replace(result, '\"area\": ', '\"area\": 1')"
        )
        store._db.commit()
        contents = ps.load_project(store)
    assert contents.fits == {} and "not reproduced" in contents.problems[0]
    with pytest.raises(ValueError, match="different spectrum"):
        other, _ = spectrum_and_fits(sources.SYN_X, seed=9)
        with new_store(tmp_path, "second") as store:
            ps.save_fits(store, other, fits)
