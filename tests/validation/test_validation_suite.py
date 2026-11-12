"""The consolidated validation runner: structure, determinism and failure reporting.

The statistical content is produced by ``scripts/validation/run_validation.py`` with many trials;
here a short run checks that the suite itself works and is reproducible.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from nsw.core import ProcessingError
from nsw.services import validation_suite as vs


@pytest.fixture(scope="module")
def run() -> vs.ValidationRun:
    return vs.run_suite(trials=40, seed=123)


def test_suite_covers_every_engine_and_separates_categories(run: vs.ValidationRun) -> None:
    studies = {c.study.split(":")[0].split(" (")[0] for c in run.checks}
    assert studies >= {
        "peak search",
        "peak fit",
        "energy calibration",
        "resolution model FWHM² = a + bE",
        "efficiency curve and activity",
        "nuclide candidates",
    }
    characterised = [c for c in run.checks if c.category == "characterised"]
    assert characterised and all(c.passed is None for c in characterised)
    assert all(c.passed is not None for c in run.checks if c.category == "checked")
    # known limitation stays visible instead of being tuned into a pass
    assert any("biased high" in c.expectation for c in characterised)
    assert run.n_checked == len(run.checks) - len(characterised)
    assert run.failed == ()


def test_same_seed_is_bit_identical_and_another_seed_differs(run: vs.ValidationRun) -> None:
    again = vs.run_suite(trials=40, seed=123)
    assert again.digest() == run.digest()
    (check,) = vs.repeatability(run, again)
    assert check.passed and "bit-identical" in check.expectation
    other = vs.run_suite(trials=40, seed=456)
    assert other.digest() != run.digest()
    (check,) = vs.repeatability(run, other)
    assert check.passed and check.value == 0.0


def test_report_formats_state_that_the_data_are_synthetic(
    run: vs.ValidationRun, tmp_path: Path
) -> None:
    text = run.to_markdown()
    assert "Synthetic data only" in text and "not a validation of any detector" in text
    assert f"`{run.digest()}`" in text and "| characterised |" in text
    doc = json.loads(run.to_json())
    assert doc["digest"] == run.digest() and doc["trials"] == 40
    assert len(doc["checks"]) == len(run.checks) and "synthetic only" in doc["data"]


def test_a_failing_metric_is_reported_as_failed(run: vs.ValidationRun) -> None:
    broken = vs.Check("demo", "coverage", "checked", 0.2, "0.683", False)
    bad = replace(run, checks=(*run.checks, broken))
    assert bad.failed == (broken,) and "| FAIL |" in bad.to_markdown()
    assert bad.digest() != run.digest()
    (check,) = vs.repeatability(bad, replace(bad, seed=999))
    assert check.passed is False
    # the helper criteria themselves: 30 % coverage in 400 trials is far outside 5 sigma
    pulls = [0.5] * 120 + [2.0] * 280
    assert vs._coverage_check("s", "m", pulls).passed is False
    assert vs._bias_check("s", "m", [1.1, 1.1001, 1.0999, 1.1], 1.0).passed is False
    assert vs._rate_check("s", "m", 39, 40, 1.0).passed is False


def test_too_few_trials_are_refused() -> None:
    with pytest.raises(ProcessingError, match="at least 40 trials"):
        vs.run_suite(trials=10)
