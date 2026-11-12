"""Assisted energy calibration: suggestions, checks before fitting, outliers after."""

from __future__ import annotations

import pytest

from nsw.core.errors import ProcessingError
from nsw.services import assisted_calibration as ac
from nsw.services import calibration as cs
from nsw.simulation import sources
from nsw.simulation.reference import fictitious_library
from tests.conftest import make_raw
from tests.unit.test_identification_service import real_like_library, spectrum_and_fits


@pytest.fixture(scope="module")
def syn_cal():  # type: ignore[no-untyped-def]
    spectrum, fits = spectrum_and_fits(sources.SYN_CAL)
    return spectrum, cs.peak_rows(fits)


def test_suggestions_recover_the_true_lines(syn_cal) -> None:  # type: ignore[no-untyped-def]
    """Every automatic suggestion is the true line; ambiguous peaks (the fictitious
    library places decoy lines near the true ones) still list the true line."""
    spectrum, rows = syn_cal
    cal = spectrum.energy_calibration
    c0, c1 = cal.coefficients[:2]
    suggestions = ac.suggest(rows, fictitious_library(), c1 * 1.005, c0, rel_uncertainty=0.004)
    unique = [s for s in suggestions if s.unique is not None]
    assert unique
    for s in suggestions:
        true_e = float(cal.energy_at(s.channel))
        if s.unique is not None:
            assert s.unique.energy_kev == pytest.approx(true_e, abs=1.0)
        elif s.lines:
            assert any(abs(line.energy_kev - true_e) < 1.0 for line in s.lines)
    assert "filled in" in ac.suggestion_summary(suggestions)


def test_wrong_guess_gives_no_or_ambiguous_suggestions(syn_cal) -> None:  # type: ignore[no-untyped-def]
    _, rows = syn_cal
    suggestions = ac.suggest(rows, fictitious_library(), 7.3, 0.0, rel_uncertainty=0.01)
    assert sum(1 for s in suggestions if s.unique is not None) < len(rows) // 2
    with pytest.raises(ProcessingError):
        ac.suggest(rows, fictitious_library(), 0.0, 0.0)


def test_library_kind_rules() -> None:
    measured = make_raw([1, 2, 3])
    assert "fictitious one must not" in ac.library_missing(measured, fictitious_library())[0]
    assert ac.library_missing(measured, real_like_library()) == ()


def test_checks_before_and_after_fitting(syn_cal) -> None:  # type: ignore[no-untyped-def]
    spectrum, rows = syn_cal
    cal = spectrum.energy_calibration
    points = [cs.make_point(r, float(cal.energy_at(r.centroid)), 0.01, "truth") for r in rows[:4]]
    assert ac.assignment_problems(points, spectrum, 1, approx_gain=None) == []
    dup = [*points, cs.make_point(rows[5], points[0].reference_value, 0.01, "truth")]
    problems = ac.assignment_problems(dup, spectrum, 1, approx_gain=None)
    assert any("given to 2 peaks" in p for p in problems)
    assert any(
        "needs at least 3" in p for p in ac.assignment_problems(points[:2], spectrum, 2, None)
    )
    narrow = [points[0], points[1]] if abs(points[0].channel - points[1].channel) < 300 else []
    if narrow:
        assert any("extrapolated" in p for p in ac.assignment_problems(narrow, spectrum, 1, 1.0))
    good = [cs.make_point(r, float(cal.energy_at(r.centroid)), 0.05, "truth") for r in rows]
    fit = cs.calibrate(good, 1)
    assert ac.outlier_messages(fit) == []
    wrong = list(good)
    wrong[2] = cs.make_point(rows[2], good[2].reference_value + 15.0, 0.05, "wrong line")
    messages = ac.outlier_messages(cs.calibrate(wrong, 1))
    assert any("wrong line" in m for m in messages)
