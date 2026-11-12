"""Parser tests for every supported format, on fabricated files with known content."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from nsw.core import (
    CalibrationNotVerifiedError,
    CalibrationOrigin,
    CalibrationStatus,
    DataOrigin,
    SpectrumKind,
)
from nsw.io import MalformedFileError, UnsupportedFormatError, read_spectrum, registered_formats
from nsw.io.base import parse_lynx_timestamp, sha256_bytes
from tests.fixtures import builders as b

COUNTS = [0, 3, 250, 100000, 7, 0, 1, 0]


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ------------------------------------------------------------------ timestamps
def test_timestamp_parsing_is_explicit_about_zone() -> None:
    t = parse_lynx_timestamp("Tue Mar 10 09:15:27 GMT+0530 2026")
    assert t.replace(tzinfo=None) == datetime(2026, 3, 10, 9, 15, 27)
    assert t.utcoffset() == timedelta(hours=5, minutes=30)
    assert parse_lynx_timestamp("Sat Jan  3 01:02:03 GMT-0800 2026").utcoffset() == timedelta(
        hours=-8
    )


@pytest.mark.parametrize("bad", ["", "2026-03-10 09:15:27", "Tue Foo 10 09:15:27 GMT+0530 2026"])
def test_bad_timestamps(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_lynx_timestamp(bad)


# ------------------------------------------------------------------ Lynx CSV
def test_csv_import_preserves_counts_and_metadata(tmp_path: Path) -> None:
    data = b.lynx_csv(COUNTS)
    s = read_spectrum(write(tmp_path, "run 30sec.csv", data))
    np.testing.assert_array_equal(s.counts, COUNTS)
    assert s.counts.dtype == np.int64
    assert (s.kind, s.origin) == (SpectrumKind.RAW, DataOrigin.EXPERIMENTAL)
    assert (s.live_time_s, s.real_time_s) == (100.0, 101.5)
    assert s.start_time_local == datetime(2026, 3, 10, 9, 15, 27)
    assert s.source is not None
    assert s.source.filename == "run 30sec.csv"  # original name preserved verbatim
    assert s.source.sha256 == sha256_bytes(data) and s.source.size_bytes == len(data)
    assert s.source.adapter == "LynxCsvAdapter"
    assert s.time_quantum_s == 0.0 and s.detector_id is None


def test_csv_vendor_calibration_is_kept_but_never_usable(tmp_path: Path) -> None:
    s = read_spectrum(write(tmp_path, "a.csv", b.lynx_csv(COUNTS, cal=(-1.5, 0.25, 0.0))))
    cal = s.embedded_calibration
    assert cal is not None and cal.coefficients == (-1.5, 0.25, 0.0)
    assert cal.origin is CalibrationOrigin.IMPORTED_VENDOR
    assert s.calibration_status is CalibrationStatus.UNVERIFIED
    assert s.energy_calibration is None
    with pytest.raises(CalibrationNotVerifiedError):
        s.energies()
    assert "verified energy calibration" in s.missing_metadata()


def test_csv_whole_second_times_are_flagged(tmp_path: Path) -> None:
    s = read_spectrum(write(tmp_path, "a.csv", b.lynx_csv(COUNTS, live=300, real=305)))
    assert s.time_quantum_s == 1.0
    assert any("whole seconds" in w for w in s.warnings)


def test_csv_accepts_lf_newlines_and_bom(tmp_path: Path) -> None:
    data = b"\xef\xbb\xbf" + b.lynx_csv(COUNTS, newline="\n")
    np.testing.assert_array_equal(read_spectrum(write(tmp_path, "a.csv", data)).counts, COUNTS)


def _csv_lines(**kw: object) -> list[str]:
    return b.lynx_csv(COUNTS, **kw).decode().split("\r\n")[:-1]  # type: ignore[arg-type]


def _join(lines: list[str]) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda L: L[:7], "no channel data"),
        (lambda L: [L[0], "Something else, 1", *L[2:]], "header line 2"),
        (lambda L: ["Start Time, yesterday", *L[1:]], "start time"),
        (lambda L: [L[0], "Energy calibration, none", *L[2:]], "calibration"),
        (lambda L: [*L[:2], "Live Time (s), abc", *L[3:]], "live time"),
        (lambda L: [*L[:9], *L[10:]], "expected channel 3"),
        (lambda L: [*L[:8], "2, 1.0, -5", *L[9:]], "not a non-negative integer"),
        (lambda L: [*L[:8], "2, 1.0, 2.5", *L[9:]], "not a non-negative integer"),
        (lambda L: [*L[:8], "2, 1.0, NaN", *L[9:]], "not a non-negative integer"),
        (lambda L: [*L[:8], "2, 1.0", *L[9:]], "expected 3 columns"),
        (lambda L: [*L[:8], "", *L[8:]], "blank line"),
    ],
)
def test_malformed_csv_gives_explicit_diagnostic(tmp_path: Path, mutate, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(MalformedFileError, match=message) as err:
        read_spectrum(write(tmp_path, "bad.csv", _join(mutate(_csv_lines()))))
    assert "bad.csv" in str(err.value)


def test_csv_live_time_exceeding_real_time_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MalformedFileError, match="exceeds real time"):
        read_spectrum(write(tmp_path, "a.csv", b.lynx_csv(COUNTS, live=100.5, real=90.25)))


def test_csv_non_utf8_rejected(tmp_path: Path) -> None:
    data = b.lynx_csv(COUNTS).replace(b"Spectrum", b"Spectr\xff\xfem")
    with pytest.raises(MalformedFileError, match="UTF-8"):
        read_spectrum(write(tmp_path, "a.csv", data))


# ------------------------------------------------------------------ Lynx AVA
def test_ava_import(tmp_path: Path) -> None:
    data = b.lynx_ava(COUNTS)
    s = read_spectrum(write(tmp_path, "x.ava", data))
    np.testing.assert_array_equal(s.counts, COUNTS)
    assert (s.live_time_s, s.real_time_s) == (100.0, 101.5)
    assert s.start_time is not None and s.start_time.utcoffset() == timedelta(hours=5, minutes=30)
    assert s.start_time_local == datetime(2026, 3, 10, 9, 15, 27, 892000)  # ms precision kept
    assert s.detector_id == "HPGe@Lynx-II-12345"
    assert (s.sample_name, s.sample_description) == ("sample-A", "a description")
    assert s.embedded_calibration is not None
    assert s.embedded_calibration.coefficients == (-1.5, 0.25, 0.0)
    assert s.calibration_status is CalibrationStatus.UNVERIFIED
    assert s.metadata["preset_live_time_s"] == "1800"
    assert s.source is not None and s.source.sha256 == sha256_bytes(data)


def test_ava_preserves_non_unique_parameters(tmp_path: Path) -> None:
    s = read_spectrum(write(tmp_path, "x.ava", b.lynx_ava(COUNTS)))
    assert s.metadata["[61] Status"] == "2048" and s.metadata["[61] Status #2"] == "0"
    assert s.metadata["[81] Detector Name"] == "Lynx-II"
    assert s.metadata["[0] Detector Name"] == "HPGe"


def test_ava_warnings(tmp_path: Path) -> None:
    s = read_spectrum(write(tmp_path, "x.ava", b.lynx_ava(COUNTS, gain=8192)))
    assert any("conversion gain" in w for w in s.warnings)
    s = read_spectrum(write(tmp_path, "y.ava", b.lynx_ava(COUNTS, start_ms=None, start=None)))
    assert s.start_time is None and "acquisition start time" in s.missing_metadata()
    s = read_spectrum(write(tmp_path, "z.ava", b.lynx_ava(COUNTS, start_ms=1773114327892 + 5000)))
    assert any("disagree" in w for w in s.warnings)
    s = read_spectrum(write(tmp_path, "c.ava", b.lynx_ava(COUNTS, cal=(1.0,))))
    assert s.embedded_calibration is None and s.calibration_status is CalibrationStatus.NONE


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b.lynx_ava(COUNTS)[:-30], "invalid XML"),
        (
            b.lynx_ava(COUNTS).replace(b"<spectrum", b"<spec").replace(b"</spectrum", b"</spec"),
            "no <spectrum>",
        ),
        (b.lynx_ava([1, "x", 3]), "non-integer"),
        (b.lynx_ava([]), "no channel data"),
        (b.lynx_ava(COUNTS, live="abc"), "not a number"),
        (b.lynx_ava([1, -2, 3]), "negative counts"),
        (b.lynx_ava(COUNTS, live=200, real=100.5), "exceeds real time"),
        (b.lynx_ava(COUNTS, prolog='<!DOCTYPE x [<!ENTITY a "b">]>'), "not allowed"),
    ],
)
def test_malformed_ava(tmp_path: Path, data: bytes, message: str) -> None:
    with pytest.raises(MalformedFileError, match=message):
        read_spectrum(write(tmp_path, "bad.ava", data))


# ------------------------------------------------------------------ Canberra CNF
CNF_COUNTS = [(i * 37) % 1000 for i in range(512)]


def test_cnf_import(tmp_path: Path) -> None:
    data = b.canberra_cnf(CNF_COUNTS, live=1200.0, real=1240.0, sample="SYN-CAL-1200sec")
    s = read_spectrum(write(tmp_path, "x.cnf", data))
    np.testing.assert_array_equal(s.counts, CNF_COUNTS)
    assert s.n_channels == 512
    assert (s.live_time_s, s.real_time_s) == (1200.0, 1240.0)
    assert s.start_time == datetime(2026, 3, 10, 9, 15, 27) and s.start_time.tzinfo is None
    assert s.sample_name == "SYN-CAL-1200sec"
    assert s.embedded_calibration is not None
    assert s.embedded_calibration.coefficients == pytest.approx((-1.5, 0.25, 0.0))
    assert s.calibration_status is CalibrationStatus.UNVERIFIED
    assert s.time_quantum_s == 1.0 and any("whole seconds" in w for w in s.warnings)


def test_cnf_fractional_times_are_not_flagged_as_rounded(tmp_path: Path) -> None:
    s = read_spectrum(write(tmp_path, "x.cnf", b.canberra_cnf(CNF_COUNTS, live=10.25, real=11.5)))
    assert (s.live_time_s, s.real_time_s, s.time_quantum_s) == (10.25, 11.5, 0.0)


def test_cnf_large_counts_do_not_overflow(tmp_path: Path) -> None:
    counts = [0] * 255 + [4_000_000_000]
    s = read_spectrum(write(tmp_path, "x.cnf", b.canberra_cnf(counts)))
    assert int(s.counts[-1]) == 4_000_000_000


def test_malformed_cnf(tmp_path: Path) -> None:
    good = b.canberra_cnf(CNF_COUNTS)
    with pytest.raises(MalformedFileError, match=r"do not fit|truncated|beyond"):
        read_spectrum(write(tmp_path, "t.cnf", good[: len(good) - 500]))
    with pytest.raises(MalformedFileError, match="channel data"):
        read_spectrum(
            write(tmp_path, "n.cnf", b.canberra_cnf(CNF_COUNTS, include_data_section=False))
        )
    zero = bytearray(good)
    zero[0x800 + 0xBA] = 0
    with pytest.raises(MalformedFileError, match="zero"):
        read_spectrum(write(tmp_path, "z.cnf", bytes(zero)))
    with pytest.raises(MalformedFileError, match="implausible live"):
        read_spectrum(write(tmp_path, "l.cnf", b.canberra_cnf(CNF_COUNTS, live=-5.0)))


# ------------------------------------------------------------------ registry
def test_format_is_detected_from_content_not_extension(tmp_path: Path) -> None:
    assert read_spectrum(write(tmp_path, "a.dat", b.lynx_csv(COUNTS))).source.adapter == (  # type: ignore[union-attr]
        "LynxCsvAdapter"
    )
    assert read_spectrum(write(tmp_path, "b.csv", b.lynx_ava(COUNTS))).source.adapter == (  # type: ignore[union-attr]
        "LynxAvaAdapter"
    )
    assert read_spectrum(write(tmp_path, "c.txt", b.canberra_cnf(CNF_COUNTS))).source.adapter == (  # type: ignore[union-attr]
        "CanberraCnfAdapter"
    )


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("doc.pdf", b"%PDF-1.7\n...", "PDF"),
        ("generic.csv", b"channel,counts\n1,5\n2,7\n", "no supported spectrum format"),
        ("plain.txt", b"5\n7\n9\n", "no supported spectrum format"),
        ("ortec.spe", b"$SPEC_ID:\nx\n$DATA:\n0 3\n1\n2\n3\n4\n", "no supported spectrum format"),
        ("fake.cnf", b"\x00" * 5000, "no supported spectrum format"),
        ("img.ava", b"\x89PNG\r\n\x1a\n", "PNG"),
    ],
)
def test_unsupported_content_is_never_silently_accepted(
    tmp_path: Path, name: str, data: bytes, message: str
) -> None:
    with pytest.raises(UnsupportedFormatError, match=message):
        read_spectrum(write(tmp_path, name, data))


def test_empty_and_missing_files(tmp_path: Path) -> None:
    with pytest.raises(MalformedFileError, match="empty"):
        read_spectrum(write(tmp_path, "empty.csv", b""))
    with pytest.raises(MalformedFileError, match="cannot read"):
        read_spectrum(tmp_path / "does-not-exist.csv")


def test_only_verified_formats_are_registered() -> None:
    assert registered_formats() == (
        "Lynx XML (.ava)",
        "Lynx CSV export",
        "Canberra CAM (.cnf)",
        "NSW synthetic spectrum (JSON)",
    )


def test_import_does_not_modify_the_file(tmp_path: Path) -> None:
    p = write(tmp_path, "a.csv", b.lynx_csv(COUNTS))
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    read_spectrum(p)
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before
