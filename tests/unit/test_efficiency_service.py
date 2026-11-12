"""The efficiency/activity service: gate for experimental data, synthetic workflow."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from nsw.services import efficiency as es
from nsw.simulation import presets, sources
from nsw.simulation.generator import SyntheticSpec, generate
from tests.conftest import make_raw

REF = "arbitrary synthetic test value"
T0 = sources.REFERENCE_TIME
MODEL = sources.SYNTHETIC_EFFICIENCY


def synthetic_spectrum() -> es.Spectrum:  # type: ignore[name-defined]
    spec = SyntheticSpec(
        synthetic_id="eff-service",
        live_time_s=2000.0,
        real_time_s=2000.0,
        seed=1,
        lines=sources.line_rates(sources.SYN_CAL, MODEL, T0, 2000.0),
        **presets.HPGE_BASE,
    )
    return generate(spec).spectrum


def lines_and_areas(source: sources.SyntheticSource, live: float) -> tuple[list, list]:  # type: ignore[type-arg]
    lines = [es.GammaLine(ln.energy_kev, ln.emission_probability, 0.0, REF) for ln in source.lines]
    areas = []
    for ln in lines:
        n = MODEL.efficiency(ln.energy_kev) * ln.emission_probability * source.activity_bq * live
        areas.append((n, math.sqrt(n)))
    return lines, areas


def test_experimental_spectra_are_refused_with_the_list_of_missing_inputs() -> None:
    s = make_raw(np.full(100, 10), start_time=datetime(2026, 3, 10, 10, 0, 0))
    missing = es.missing_inputs(s)
    assert not es.EXPERIMENTAL_QUANTIFICATION_ENABLED
    for needle in (
        "certified source activity",
        "geometry",
        "detector model",
        "nuclear decay data",
        "verified energy calibration",
        "authorisation by the project owner",
    ):
        assert any(needle in m for m in missing), needle
    lines, areas = lines_and_areas(sources.SYN_CAL, 2000.0)
    cert = es.ReferenceSource("x", 2e4, 200.0, T0, 6e7, 0.0, REF, True)
    timing = es.timing_of(s)
    with pytest.raises(es.ProcessingError, match="not available for this experimental spectrum"):
        es.efficiency_curve(s, areas, lines, cert, timing)
    curve = es.efficiency_curve(
        synthetic_spectrum(), areas, lines, cert, es.MeasurementTiming(T0, 2000.0, 2000.0)
    )
    with pytest.raises(es.ProcessingError, match="An activity is not available"):
        es.activity(s, areas[:1], lines[:1], curve, timing, 6e7)
    no_time = make_raw(np.full(100, 10), live_time_s=None, real_time_s=None)
    assert any("start time" in m for m in es.missing_inputs(no_time))
    assert any("live and real time" in m for m in es.missing_inputs(no_time))
    assert es.result_label(s) == "EXPERIMENTAL"


def test_synthetic_workflow_and_labels() -> None:
    s = synthetic_spectrum()
    assert es.missing_inputs(s) == () and es.result_label(s).startswith("SYNTHETIC")
    with pytest.raises(es.ProcessingError, match="start time"):
        es.timing_of(s)  # synthetic spectra carry no start time
    timing = es.timing_of(s, T0)
    lines, areas = lines_and_areas(sources.SYN_CAL, 2000.0)
    cert = es.ReferenceSource(
        "SYN-CAL", 2e4, 200.0, T0, sources.SYN_CAL.half_life_s, 0.0, REF, True
    )
    with pytest.raises(es.ProcessingError, match="marked as synthetic"):
        real_cert = es.ReferenceSource("c", 2e4, 200.0, T0, 6e7, 0.0, "certificate 123")
        es.efficiency_curve(s, areas, lines, real_cert, timing)
    with pytest.raises(es.ProcessingError, match="one gamma line per peak area"):
        es.efficiency_curve(s, areas[:3], lines, cert, timing)
    curve = es.efficiency_curve(s, areas, lines, cert, timing, geometry="synthetic")
    f = sources.SYN_CAL.in_measurement_factor(2000.0)
    value, _, _ = curve.efficiency(500.0)
    assert value[0] == pytest.approx(MODEL.efficiency(500.0) / f, rel=1e-6)
    rows = dict(es.curve_rows(s, curve))
    assert rows["Status"].startswith("SYNTHETIC") and rows["Geometry"] == "synthetic"
    assert "absolute" in rows["Quantity"] and "extrapolated" in rows["Valid range"]

    sample = sources.SYN_X
    start = T0 + timedelta(days=10)
    s_lines, _ = lines_and_areas(sample, 1000.0)
    scale = sample.activity_at(start) / sample.activity_bq * sample.in_measurement_factor(1000.0)
    s_areas = []
    for ln in s_lines:
        n = (
            MODEL.efficiency(ln.energy_kev)
            / f
            * ln.emission_probability
            * sample.activity_bq
            * scale
            * 1000.0
        )
        s_areas.append((n, math.sqrt(n)))
    result = es.activity(
        s,
        s_areas,
        s_lines,
        curve,
        es.MeasurementTiming(start, 1000.0, 1000.0),
        sample.half_life_s,
        reference_time=sample.reference_time,
    )
    assert result.activity.value == pytest.approx(sample.activity_bq, rel=1e-6)
    assert result.reference_time == sample.reference_time and result.consistent
