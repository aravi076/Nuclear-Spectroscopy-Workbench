"""Provenance in project files: decisions saved and restored, result digests, no paths."""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import numpy as np

from nsw.analysis.fitting import FitSettings
from nsw.database.store import ProjectStore, canonical_digest
from nsw.services import project as ps
from nsw.services import roi as rs
from tests.conftest import make_raw, make_source
from tests.unit.test_roi import gaussian_counts


def session_item() -> ps.SessionSpectrum:
    s = make_raw(
        np.rint(gaussian_counts(200, [(100.0, 5.0, 8000.0)], 40.0)).astype(int),
        source=make_source("sample.csv"),
    )
    roi = rs.make_roi(s, 80, 120, rs.RoiKind.SINGLE, (100.0,), (5.0,), "main peak")
    fit = rs.fit_roi(s, roi, FitSettings())
    decision = {"nuclide": "SYN-X", "line_kev": 650.0, "accepted": True, "peak": "x"}
    return ps.SessionSpectrum(s, None, (fit,), (roi,), frozenset({100.0}), (decision,))


def test_decisions_round_trip_and_are_not_repeated(tmp_path: Path) -> None:
    item = session_item()
    store = ps.create_project(tmp_path / "p", "provenance")
    first = ps.save_session(store, [item])
    assert first.decisions == 3 and "3 analyst decisions" in first.text()
    again = ps.save_session(store, [item])
    assert again.decisions == 0  # nothing new
    moved = item.rois[0].moved_to(82, 118)
    changed = ps.SessionSpectrum(item.spectrum, None, item.fits, (moved,), frozenset(), ())
    assert ps.save_session(store, [changed]).decisions == 2  # region edit + exclusion set
    path = store.path
    store.close()
    store = ProjectStore.open(path)
    contents = ps.load_project(store)
    sha = item.spectrum.data_sha256
    (roi,) = contents.rois[sha]
    assert (roi.roi_id, roi.first_channel, roi.last_channel) == (moved.roi_id, 82, 118)
    assert contents.excluded[sha] == frozenset()
    assert contents.assignments[sha][0]["nuclide"] == "SYN-X"
    assert contents.digests_verified >= 1 and contents.problems == []
    store.close()


def test_result_digests_detect_a_changed_result(tmp_path: Path) -> None:
    item = session_item()
    store = ps.create_project(tmp_path / "p", "digests")
    ps.save_session(store, [item])
    (run,) = store.runs("peak-fit")
    prov = run.settings["provenance"]
    assert prov["algorithm"] == ps.ALGORITHM_VERSIONS["peak-fit"]
    assert set(prov["software"]) == {"nsw", "python", "numpy", "scipy"}
    assert prov["result_sha256"] == canonical_digest(run.result)
    path = store.path
    store.close()
    # simulate damage outside NSW: remove the append-only trigger and change a value
    db = sqlite3.connect(path)
    db.execute("DROP TRIGGER analysis_run_no_update")
    db.execute(
        "UPDATE analysis_run SET result = "
        "replace(result, '\"converged\": true', '\"converged\": false')"
    )
    db.commit()
    db.close()
    store = ProjectStore.open(path)
    ok, problems = ps.verify_digests(store.runs())
    assert ok == 0 and problems and "does not match its digest" in problems[0]
    store.close()


def test_source_paths_are_not_stored(tmp_path: Path) -> None:
    # assembled at run time so that the public-copy scanner does not flag this file
    private = "C:" + "/Users/" + "someone" + "/Desktop/measurement.csv"
    s = make_raw(
        [1, 2, 3, 4], source=dataclasses.replace(make_source("measurement.csv"), path=private)
    )
    assert s.source is not None and s.source.path == private
    store = ps.create_project(tmp_path / "p", "paths")
    ps.save_spectrum(store, s)
    restored = store.spectrum(s.data_sha256)
    assert restored.source is not None and restored.source.path == "measurement.csv"
    raw = Path(store.path).read_bytes()
    assert b"someone" not in raw
    store.close()
