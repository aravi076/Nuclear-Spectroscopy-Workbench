"""Measurement identity (content-based grouping) and dataset integrity manifests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nsw.core import MeasurementStatus
from nsw.io import FindingKind, analyse_identity, read_spectrum
from nsw.io.integrity import create_manifest, verify_manifest
from nsw.services.dataset import scan_dataset
from tests.fixtures import builders as b

A = [(i * 7) % 50 for i in range(256)]
A2 = [c * 2 for c in A]  # cumulative continuation of A
A3 = [c * 3 + 1 for c in A]
OTHER = [(i * 11) % 40 + 1 for i in range(256)]
T0 = "Mon Apr 27 14:49:03 GMT+0530 2026"
T1 = "Mon Apr 27 16:00:00 GMT+0530 2026"


def put(folder: Path, name: str, data: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(data)
    return p


def test_same_data_in_three_formats_is_one_snapshot(tmp_path: Path) -> None:
    files = [
        put(tmp_path, "run.cnf", b.canberra_cnf(A, live=100.0, real=102.0)),
        put(tmp_path, "run.csv", b.lynx_csv(A, live=100, real=101.5)),
        put(tmp_path, "run.ava", b.lynx_ava(A, live=100, real=101.5)),
    ]
    report = analyse_identity([read_spectrum(p) for p in files])
    assert len(report.snapshots) == 1 and len(report.acquisitions) == 1
    snap = report.snapshots[0]
    assert len(snap.files) == 3
    # AVA preferred: richest metadata and unrounded times
    assert snap.preferred.source is not None and snap.preferred.source.filename == "run.ava"
    assert snap.preferred.measurement_status is MeasurementStatus.INDEPENDENT
    assert report.findings == ()


def test_successive_saves_are_not_independent(tmp_path: Path) -> None:
    files = [
        put(tmp_path, "s 300 sec.csv", b.lynx_csv(A3, start=T0, live=300, real=303.5)),
        put(tmp_path, "s 100 sec.csv", b.lynx_csv(A, start=T0, live=100, real=101.5)),
        put(tmp_path, "s 200 sec.csv", b.lynx_csv(A2, start=T0, live=200, real=202.5)),
        put(tmp_path, "other 100 sec.csv", b.lynx_csv(OTHER, start=T1, live=100, real=101.5)),
    ]
    report = analyse_identity([read_spectrum(p) for p in files])
    assert len(report.acquisitions) == 2 and len(report.snapshots) == 4
    assert report.n_independent == 2  # the final save of the series + the separate run
    series = next(a for a in report.acquisitions if a.is_series)
    assert series.cumulative_verified
    assert [s.live_time_s for s in series.snapshots] == [100.0, 200.0, 300.0]
    assert [s.preferred.measurement_status for s in series.snapshots] == [
        MeasurementStatus.INTERMEDIATE_SAVE,
        MeasurementStatus.INTERMEDIATE_SAVE,
        MeasurementStatus.INDEPENDENT,
    ]
    assert series.final.live_time_s == 300.0
    (finding,) = report.findings_of(FindingKind.SUCCESSIVE_SAVES)
    assert "only the last save is an independent measurement" in finding.message


def test_cnf_start_time_matches_csv_within_tolerance(tmp_path: Path) -> None:
    # CNF carries sub-second local time without zone, CSV whole seconds with zone.
    files = [
        put(tmp_path, "a.cnf", b.canberra_cnf(A, start=datetime(2026, 4, 27, 14, 49, 3, 800000))),
        put(tmp_path, "b.csv", b.lynx_csv(A2, start=T0, live=200, real=203.5)),
    ]
    report = analyse_identity([read_spectrum(p) for p in files])
    assert len(report.acquisitions) == 1 and report.acquisitions[0].cumulative_verified


def test_same_start_but_not_cumulative_is_left_unclassified(tmp_path: Path) -> None:
    files = [
        put(tmp_path, "a.csv", b.lynx_csv(A, start=T0, live=100, real=101.5)),
        put(tmp_path, "b.csv", b.lynx_csv(OTHER, start=T0, live=200, real=202.5)),
    ]
    report = analyse_identity([read_spectrum(p) for p in files])
    assert report.n_independent == 0
    assert all(
        s.preferred.measurement_status is MeasurementStatus.UNCLASSIFIED for s in report.snapshots
    )
    assert len(report.findings_of(FindingKind.INCONSISTENT_SERIES)) == 1


def test_files_are_not_paired_by_name(tmp_path: Path) -> None:
    files = [
        put(tmp_path, "run 100sec.csv", b.lynx_csv(A, start=T0, live=100, real=101.5)),
        put(tmp_path, "run 100sec.cnf", b.canberra_cnf(OTHER, start=datetime(2026, 4, 28, 9, 0))),
    ]
    report = analyse_identity([read_spectrum(p) for p in files])
    assert len(report.snapshots) == 2 and report.n_independent == 2
    (conflict,) = report.findings_of(FindingKind.NAME_CONTENT_CONFLICT)
    assert "run 100sec.csv" in conflict.message and "run 100sec.cnf" in conflict.message


@pytest.mark.parametrize(
    ("name", "live", "flagged"),
    [
        ("54Mn-1500sec.csv", 1200.5, True),
        ("54Mn-600sec.csv", 603.153, False),  # within 1 % + 1 s
        ("1200s137Cs.csv", 1200.5, False),  # "137Cs" must not be read as a duration
        ("30s 60Co.csv", 30.5, False),
        ("152Eu+120 Sec.csv", 60.5, True),
        ("background23april.csv", 1800.5, False),  # no duration in the name
    ],
)
def test_duration_in_filename_is_only_a_diagnostic(
    tmp_path: Path, name: str, live: float, flagged: bool
) -> None:
    s = read_spectrum(put(tmp_path, name, b.lynx_csv(A, live=live, real=live + 5.25)))
    report = analyse_identity([s])
    assert bool(report.findings_of(FindingKind.DURATION_NAME_MISMATCH)) is flagged
    assert report.n_independent == 1  # identity itself is unaffected by the name


def test_byte_identical_duplicates_reported(tmp_path: Path) -> None:
    data = b.lynx_csv(A)
    report = analyse_identity(
        [read_spectrum(put(tmp_path, n, data)) for n in ("x-60se.csv", "x-60sec.csv")]
    )
    assert len(report.snapshots) == 1
    assert len(report.findings_of(FindingKind.DUPLICATE_FILE)) == 1


def test_timing_disagreement_between_precise_copies(tmp_path: Path) -> None:
    files = [
        put(tmp_path / "d1", "t.csv", b.lynx_csv(A, live=300, real=304.53)),
        put(tmp_path / "d2", "t.csv", b.lynx_csv(A, live=300, real=304.99)),
    ]
    report = analyse_identity([read_spectrum(p) for p in files])
    assert len(report.findings_of(FindingKind.TIMING_DISAGREEMENT)) == 1


# ------------------------------------------------------------------ folder scan
def test_scan_reports_every_file_and_explicit_failures(tmp_path: Path) -> None:
    put(tmp_path / "sub", "ok.csv", b.lynx_csv(A))
    put(tmp_path, "ok.ava", b.lynx_ava(OTHER, start=T1, start_ms=None))
    put(tmp_path, "notes.pdf", b"%PDF-1.7 ...")
    put(tmp_path, "broken.csv", b.lynx_csv(A)[:200])
    seen: list[str] = []
    scan = scan_dataset(tmp_path, progress=lambda i, n, name: seen.append(name))
    assert scan.n_files == 4 and len(scan.spectra) == 2
    reasons = {Path(f.path).name: (f.unsupported, f.reason) for f in scan.failures}
    assert reasons["notes.pdf"][0] is True and "PDF" in reasons["notes.pdf"][1]
    assert reasons["broken.csv"][0] is False
    summary = scan.summary()
    assert summary["unsupported files"] == 1 and summary["malformed files"] == 1
    assert summary["independent measurements"] == 2
    assert {"ok.csv", "ok.ava", "notes.pdf", "broken.csv"} <= set(seen)


def test_scan_can_be_cancelled(tmp_path: Path) -> None:
    for i in range(5):
        put(tmp_path, f"f{i}.csv", b.lynx_csv([i, 1, 2]))
    scan = scan_dataset(tmp_path, cancelled=lambda: True)
    assert scan.cancelled and scan.n_files == 0
    with pytest.raises(NotADirectoryError):
        scan_dataset(tmp_path / "missing")


# ------------------------------------------------------------------ integrity
def test_manifest_detects_any_change(tmp_path: Path) -> None:
    root, manifest = tmp_path / "data", tmp_path / "manifest.csv"
    put(root, "a.csv", b"one")
    put(root / "sub", "b.cnf", b"two")
    assert create_manifest(root, manifest) == 2
    assert verify_manifest(root, manifest).ok
    with pytest.raises(FileExistsError):
        create_manifest(root, manifest)  # never overwrites
    (root / "a.csv").write_bytes(b"onE")
    put(root, "c.ava", b"three")
    (root / "sub" / "b.cnf").unlink()
    report = verify_manifest(root, manifest)
    assert not report.ok
    assert (report.changed, report.added, report.removed) == (
        ("a.csv",),
        ("c.ava",),
        ("sub/b.cnf",),
    )
