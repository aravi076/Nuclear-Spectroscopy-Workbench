"""Analysis reports (PDF) and figure export (SVG, PDF, PNG)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from nsw.reporting.document import Report, Section, write_pdf
from nsw.services import efficiency as es
from nsw.services import identification as ids
from nsw.services import report as rs
from nsw.services.export import ExportError
from nsw.simulation import sources
from tests.conftest import make_raw
from tests.unit.test_identification_service import spectrum_and_fits

FIXED = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def sections(report: Report) -> dict[str, Section]:
    return {s.title: s for s in report.sections}


def full_report() -> tuple[es.Spectrum, Report]:  # type: ignore[name-defined]
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    library = ids.fictitious_library()
    identification = ids.identify(spectrum, fits, library)
    return spectrum, rs.build_report(
        spectrum, fits=fits, identification=identification, library=library, created=FIXED
    )


def test_report_contains_what_is_needed_to_reconstruct_the_result() -> None:
    spectrum, report = full_report()
    assert report.banner_kind == "synthetic" and report.banner.startswith("SYNTHETIC")
    s = sections(report)
    for title in (
        "Measurement",
        "File and data identity",
        "Detector and acquisition",
        "Energy calibration",
        "Background handling",
        "Peak fits",
        "Nuclide candidates",
        "Reference data (provenance)",
        "Methods and conventions",
        "Limitations",
        "Software and reproducibility",
    ):
        assert title in s, title
    software = dict(s["Software and reproducibility"].rows)
    assert software["Spectrum data SHA-256"] == spectrum.data_sha256
    assert software["Report created (UTC)"] == "2026-11-12T12:00:00+00:00"
    assert s["Peak fits"].table is not None and len(s["Peak fits"].table[1]) == 3
    assert s["Nuclide candidates"].table[1][0][0] == "SYN-X"  # type: ignore[index]
    assert "never an identification" in s["Methods and conventions"].text
    assert "not a validation of any detector" in s["Limitations"].text
    assert report.figure is not None and report.figure.x_label == "Energy (keV)"
    assert len(report.figure.overlays) == 3


def test_experimental_spectrum_is_labelled_provisional_and_stays_in_channels() -> None:
    measured = make_raw(np.arange(300) % 23 + 5, start_time=datetime(2026, 3, 10, 10, 0, 0))
    report = rs.build_report(measured, created=FIXED)
    assert report.banner_kind == "provisional" and "channel units" in report.banner
    assert report.figure is not None and report.figure.x_label == "Channel"
    assert "Nuclide candidates" not in sections(report)


def test_efficiency_and_activity_sections() -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    source = es.fictitious_sources()["SYN-X"]
    matches = es.match_lines(spectrum, fits, source.lines)
    from tests.validation.test_identification import curve

    c = curve()
    timing = es.timing_of(spectrum, es.start_time_for(spectrum, 0.0))
    activity = es.activity(
        spectrum,
        [(m.area, m.area_unc) for m in matches],
        [m.line for m in matches],
        c,
        timing,
        source.source.half_life_s,
        reference_time=source.source.reference_time,
    )
    report = rs.build_report(spectrum, fits=fits, efficiency=c, activity=activity, created=FIXED)
    s = sections(report)
    assert dict(s["Activity"].rows)["Status"].startswith("SYNTHETIC")
    assert "Bq" in dict(s["Activity"].rows)["Activity"]
    assert any(k == "Valid range" for k, _ in s["Efficiency calibration"].rows)


def test_pdf_is_written_reproducibly_and_paginates(tmp_path: Path) -> None:
    _, report = full_report()
    a = rs.export_report(report, tmp_path / "a.pdf")
    b = rs.export_report(report, tmp_path / "b.pdf")
    data = a.read_bytes()
    assert data.startswith(b"%PDF") and data == b.read_bytes()  # no timestamps inside
    long = Report(
        "t", "SYNTHETIC", "synthetic", [Section("s", [(f"k{i}", "v" * 300) for i in range(300)])]
    )
    out = write_pdf(long, tmp_path / "long.pdf")
    pages = len(re.findall(rb"/Type\s*/Page\b", out.read_bytes()))
    assert pages >= 5
    with pytest.raises(ExportError, match="already exists"):
        rs.export_report(report, a)
    with pytest.raises(ExportError, match="PDF"):
        rs.export_report(report, tmp_path / "x.docx")


def test_figures_in_three_formats_and_protected_folders(tmp_path: Path) -> None:
    spectrum, fits = spectrum_and_fits(sources.SYN_X)
    svg = rs.export_figure(spectrum, fits, tmp_path / "f.svg")
    root = ET.parse(svg).getroot()  # noqa: S314 - SVG written by this test, not untrusted input
    assert root.tag.endswith("svg")
    assert rs.export_figure(spectrum, fits, tmp_path / "f.pdf").read_bytes().startswith(b"%PDF")
    assert rs.export_figure(spectrum, fits, tmp_path / "f.png").read_bytes()[:4] == b"\x89PNG"
    with pytest.raises(ExportError, match="SVG, PDF or PNG"):
        rs.export_figure(spectrum, fits, tmp_path / "f.jpg")
    protected = tmp_path / "dataset"
    protected.mkdir()
    with pytest.raises(ExportError, match="read-only"):
        rs.export_figure(spectrum, fits, protected / "f.svg", [protected])
    assert not any(protected.iterdir())
