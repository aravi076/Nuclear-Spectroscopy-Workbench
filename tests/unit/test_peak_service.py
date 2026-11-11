"""Service-level tests: tables, labels and CSV export of peak results."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from scipy.special import ndtr

from nsw.services import peaks as ps
from nsw.services.export import CALIBRATION_WARNING, SYNTHETIC_NOTICE, ExportError
from nsw.simulation import presets
from nsw.simulation.generator import generate
from tests.conftest import make_raw

X = np.arange(1.0, 301.0)


def experimental() -> ps.Spectrum:
    y = 20000 * (ndtr((X + 0.5 - 150.4) / 2.0) - ndtr((X - 0.5 - 150.4) / 2.0)) + 30
    return make_raw(np.round(y).astype(np.int64))


def synthetic() -> ps.Spectrum:
    specs = {s.synthetic_id: s for s in presets.hpge_scenarios()}
    return generate(specs["hpge-overlapping-doublet"]).spectrum


def test_status_labels_distinguish_experimental_and_synthetic() -> None:
    assert "PROVISIONAL" in ps.result_status(experimental())
    assert ps.result_status(synthetic()).startswith("SYNTHETIC")
    assert ps.spectrum_name(experimental()) == "file.csv"
    assert ps.spectrum_name(synthetic()) == "hpge-overlapping-doublet"


def test_default_scales() -> None:
    w, w2 = ps.default_scales(experimental())
    assert w == pytest.approx(4.7, abs=0.5) and w2 == pytest.approx(2 * w)
    assert ps.default_scales(make_raw(np.full(300, 20))) == ps.FALLBACK_SCALES


def test_candidate_and_peak_tables_show_energy_only_when_verified() -> None:
    s = experimental()
    found = ps.search(s, ps.PeakSearchSettings(fwhm_channels=(5.0, 10.0)))
    headers, rows = ps.candidate_table(found)
    assert "Energy (keV)" not in headers and len(rows) == 1 and len(rows[0]) == len(headers)
    assert float(rows[0][headers.index("Channel")]) == pytest.approx(150.4, abs=0.1)
    (fit,) = ps.fit_all(s, found, ps.FitSettings())
    assert fit.result is not None
    headers, rows = ps.peak_table([fit.result])
    assert "Energy (keV)" not in headers and rows[0][-1] == "ok"
    assert float(rows[0][headers.index("Area (counts)")]) == pytest.approx(20000, rel=0.01)

    syn = synthetic()
    found = ps.search(syn, ps.PeakSearchSettings(fwhm_channels=(4.0, 8.0)))
    headers, rows = ps.candidate_table(found)
    assert headers[3] == "Energy (keV)"
    fits = [f.result for f in ps.fit_all(syn, found, ps.FitSettings()) if f.result is not None]
    headers, rows = ps.peak_table(fits)
    assert headers[3] == "Energy (keV)" and all(len(r) == len(headers) for r in rows)
    energies = [float(r[3]) for r in rows]
    assert any(abs(e - 600.0) < 0.05 for e in energies)
    assert any(abs(e - 603.0) < 0.05 for e in energies)


def test_fit_all_reports_progress_can_be_cancelled_and_checks_the_spectrum() -> None:
    syn = synthetic()
    found = ps.search(syn, ps.PeakSearchSettings(fwhm_channels=(4.0, 8.0)))
    seen: list[tuple[int, int, str]] = []
    fits = ps.fit_all(syn, found, ps.FitSettings(), progress=lambda *a: seen.append(a))
    assert len(fits) == len(found.groups()) and seen[-1][0] == seen[-1][1] == len(fits)
    assert ps.fit_all(syn, found, ps.FitSettings(), cancelled=lambda: True) == ()
    with pytest.raises(ps.ProcessingError, match="different spectrum"):
        ps.fit_all(experimental(), found, ps.FitSettings())


def test_group_that_cannot_be_fitted_is_reported_not_raised() -> None:
    s = experimental()
    found = ps.search(s, ps.PeakSearchSettings(fwhm_channels=(5.0,)))
    wls = ps.FitSettings(method=ps.FitMethod.POISSON, max_iterations=1)
    (fit,) = ps.fit_all(s, found, wls)
    assert fit.result is not None and not fit.result.converged
    assert ps.peak_table([fit.result])[1][0][-1] == "NOT CONVERGED"
    rows = dict(ps.fit_summary_rows(s, fit.result))
    assert rows["Outcome"].startswith("NOT CONVERGED")


def test_summary_and_evidence_rows() -> None:
    syn = synthetic()
    r = ps.fit(syn, 2380, 2428, [2398.0, 2410.0], 6.0, ps.FitSettings(step=True))
    rows = ps.fit_summary_rows(syn, r)
    keys = [k for k, _ in rows]
    d = dict(rows)
    assert d["Result status"].startswith("SYNTHETIC")
    assert "2 Gaussian(s)" in d["Model"] and "step" in d["Model"] and "common width" in d["Model"]
    assert "Step fraction" in keys and "Continuum: background_left" in keys
    assert "Runs test on residual signs" in keys
    ev = ps.evidence(syn, 2380, 2428, [2398.0, 2410.0], 6.0, ps.FitSettings())
    e = dict(ps.evidence_rows(ev))
    assert "supported" in e["Multiplet assessment"] and "FWHM" in e["Separation of neighbours"]


def test_csv_export_content_and_guards(tmp_path: Path) -> None:
    s = experimental()
    r = ps.fit(s, 130, 170, [150.0], 4.7, ps.FitSettings())
    text = ps.peaks_csv_text(s, [r])
    assert CALIBRATION_WARNING in text and "PROVISIONAL" in text and "energy_keV" not in text
    header, row = [ln for ln in text.splitlines() if not ln.startswith("#")]
    cols = header.split(",")
    cells = row.split(",")
    assert len(cols) == len(cells)
    assert float(cells[cols.index("area_counts")]) == r.peaks[0].area  # full precision
    assert cells[cols.index("quality")] == "ok"

    syn = synthetic()
    rs = ps.fit(syn, 2380, 2428, [2398.0, 2410.0], 6.0, ps.FitSettings())
    text = ps.peaks_csv_text(syn, [rs])
    assert SYNTHETIC_NOTICE in text and "energy_keV" in text and CALIBRATION_WARNING not in text

    out = ps.export_peaks_csv(s, [r], tmp_path / "p.csv")
    assert out.read_text(encoding="utf-8") == ps.peaks_csv_text(s, [r])
    with pytest.raises(ExportError, match="already exists"):
        ps.export_peaks_csv(s, [r], tmp_path / "p.csv")
    ps.export_peaks_csv(s, [r], tmp_path / "p.csv", overwrite=True)
    with pytest.raises(ExportError, match="no fit results"):
        ps.export_peaks_csv(s, [], tmp_path / "q.csv")
    with pytest.raises(ExportError, match="read-only experimental dataset"):
        ps.export_peaks_csv(s, [r], tmp_path / "q.csv", protected_roots=[tmp_path])
