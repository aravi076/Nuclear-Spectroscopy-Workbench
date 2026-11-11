"""Service-level tests of the energy-calibration use case."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from scipy.special import ndtr

from nsw.core import CalibrationStatus, ProcessingError
from nsw.core.spectrum import MeasurementStatus
from nsw.services import calibration as cs
from nsw.services import peaks as ps
from nsw.services.export import ExportError
from nsw.simulation import presets
from nsw.simulation.generator import generate
from tests.conftest import make_raw, make_source

X = np.arange(1.0, 1025.0)
SRC = "arbitrary test value"
NOW = datetime(2026, 11, 11, 12, 0, 0)


def raw(shift: float = 0.0, name: str = "a.csv", **kw: object) -> ps.Spectrum:
    y = np.full(X.size, 20.0)
    for c in (150.0, 400.0, 800.0):
        y += 20000 * (ndtr((X + 0.5 - c - shift) / 2.0) - ndtr((X - 0.5 - c - shift) / 2.0))
    return make_raw(np.round(y).astype(np.int64), source=make_source(name), **kw)


def fits_of(s: ps.Spectrum) -> list[ps.FitResult]:
    found = ps.search(s, ps.PeakSearchSettings(fwhm_channels=(5.0, 10.0)))
    return [f.result for f in ps.fit_all(s, found, ps.FitSettings()) if f.result is not None]


def fitted(s: ps.Spectrum, energies: tuple[float, ...] = (75.0, 200.0, 400.0)) -> cs.CalibrationFit:
    rows = cs.peak_rows(fits_of(s))
    points = [
        cs.make_point(r, e, 0.01, SRC, f"L{e:g}") for r, e in zip(rows, energies, strict=True)
    ]
    return cs.calibrate(points)


def test_peak_rows_exclude_unusable_fits() -> None:
    s = raw()
    fits = fits_of(s)
    rows = cs.peak_rows(fits)
    assert [round(r.centroid) for r in rows] == [150, 400, 800]
    assert all(r.quality == "ok" and r.centroid_unc > 0 for r in rows)
    flat = ps.fit(make_raw(np.full(200, 50)), 50, 150, [100.0], 4.0, ps.FitSettings())
    assert cs.peak_rows([flat]) == []  # insignificant or flagged peaks are not offered
    unconverged = ps.fit(s, 130, 170, [150.0], 4.7, ps.FitSettings(max_iterations=1))
    assert not unconverged.converged and cs.peak_rows([unconverged]) == []


def test_make_point_requires_source_and_valid_numbers() -> None:
    row = cs.peak_rows(fits_of(raw()))[0]
    p = cs.make_point(row, 75.0, 0.01, f"  {SRC} ", " label ")
    assert p.reference_source == SRC and p.label == "label"
    assert p.channel == row.centroid and p.channel_uncertainty == row.centroid_unc
    for bad_source in ("", "   "):
        with pytest.raises(ProcessingError, match="has no source"):
            cs.make_point(row, 75.0, 0.01, bad_source)
    with pytest.raises(ProcessingError, match="positive number"):
        cs.make_point(row, -1.0, 0.01, SRC)
    with pytest.raises(ProcessingError, match=">= 0"):
        cs.make_point(row, 75.0, -0.1, SRC)


def test_experimental_calibration_is_never_verified_at_this_stage() -> None:
    s = raw()
    fit = fitted(s)
    assert fit.verifiable and not cs.EXPERIMENTAL_VERIFICATION_ENABLED
    ok, reason = cs.verification(s, fit)
    assert not ok and "laboratory confirms" in reason
    record = cs.make_record(s, fit, NOW)
    assert record.status is CalibrationStatus.UNVERIFIED and record.created == NOW
    assert "PROVISIONAL" in cs.status_text(s, fit)
    applied = cs.apply_calibration(s, record)
    assert applied.calibration_status is CalibrationStatus.UNVERIFIED
    assert applied.data_sha256 == s.data_sha256  # channel data untouched
    other = raw(detector_id="det-2")
    with pytest.raises(ProcessingError, match="belongs to detector"):
        cs.apply_calibration(other, record.model_copy(update={"detector_id": "det-1"}))


def test_synthetic_calibration_can_be_verified_and_tables_are_complete(tmp_path: Path) -> None:
    specs = {sp.synthetic_id: sp for sp in presets.hpge_scenarios()}
    s = generate(specs["hpge-isolated-peaks-no-continuum"]).spectrum
    fit = fitted(s, (100.0, 300.0, 600.0, 1000.0, 1500.0))
    ok, reason = cs.verification(s, fit)
    assert ok and reason == ""
    record = cs.make_record(s, fit, NOW, note="unit test")
    assert record.is_verified and record.note.startswith("SYNTHETIC")
    two = cs.calibrate(list(fit.points[:2]))
    assert cs.verification(s, two) == (False, two.why_not_verifiable())
    assert "not verified" in cs.status_text(s, two)

    rows = dict(cs.fit_rows(s, fit))
    assert rows["Status"].startswith("SYNTHETIC") and "order 1; 5 reference points" in rows["Model"]
    assert "not rescaled" in rows["Uncertainties"]
    headers, table = cs.point_table(fit)
    assert len(table) == 5 and all(len(r) == len(headers) for r in table)
    assert table[0][headers.index("Source of reference")] == SRC
    ch, e, u = cs.curve(fit)
    assert ch[0] < fit.channel_range[0] and ch[-1] > fit.channel_range[1]
    assert np.all(np.diff(e) > 0) and np.all(u > 0)
    item = cs.CalibrationHistoryItem(NOW, "spec", True, record, fit)
    headers, table = cs.history_table([item])
    assert table[0][headers.index("Status")] == "verified"
    assert cs.order_test(list(fit.points)).justified is False

    text = cs.calibration_csv_text(s, fit)
    assert "np.float64" not in text and "# covariance row 1:" in text
    data = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert len(data) == 6 and len(data[1].split(",")) == len(data[0].split(","))
    out = cs.export_calibration_csv(s, fit, tmp_path / "c.csv")
    assert out.read_text(encoding="utf-8") == text
    with pytest.raises(ExportError, match="already exists"):
        cs.export_calibration_csv(s, fit, tmp_path / "c.csv")
    with pytest.raises(ExportError, match="read-only experimental dataset"):
        cs.export_calibration_csv(s, fit, tmp_path / "d.csv", protected_roots=[tmp_path])


def test_stability_uses_independent_spectra_only() -> None:
    a, b, shifted = raw(name="a.csv"), raw(name="b.csv"), raw(0.6, name="c.csv")
    pairs = [(a, fits_of(a)), (b, fits_of(b))]
    result, (headers, rows) = cs.stability_of_line(pairs, 400.0, 3.0)
    assert result.stable and result.n == 2 and len(rows) == 2 and headers[0] == "Spectrum"
    drift, _ = cs.stability_of_line([*pairs, (shifted, fits_of(shifted))], 400.0, 3.0)
    assert not drift.stable
    # an intermediate save of the same acquisition is not an independent measurement
    save = raw(name="save.csv").evolve(measurement_status=MeasurementStatus.INTERMEDIATE_SAVE)
    with pytest.raises(ProcessingError, match="fewer than two independent"):
        cs.stability_of_line([(a, fits_of(a)), (save, fits_of(save))], 400.0, 3.0)
    with pytest.raises(ProcessingError, match="fewer than two"):
        cs.stability_of_line(pairs, 600.0, 3.0)  # no peak there
    specs = {sp.synthetic_id: sp for sp in presets.hpge_scenarios()}
    syn = generate(specs["hpge-isolated-peaks-no-continuum"]).spectrum
    with pytest.raises(ProcessingError, match="cannot be compared"):
        cs.stability_of_line([(a, fits_of(a)), (syn, fits_of(syn))], 398.0, 3.0)
