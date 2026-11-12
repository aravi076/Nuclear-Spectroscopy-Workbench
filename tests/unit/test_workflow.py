"""Guided workflow stages: statuses and reasons, gates never relaxed."""

from __future__ import annotations

from nsw.services import workflow as wf


def by_name(stages: list[wf.Stage], name: str) -> wf.Stage:
    return next(s for s in stages if s.name == name)


def test_nothing_loaded() -> None:
    stages = wf.stages(wf.WorkflowContext())
    assert [s.number for s in stages] == list(range(1, 13))
    assert by_name(stages, "Spectrum import").status is wf.StageStatus.READY
    assert by_name(stages, "Peak fitting").status is wf.StageStatus.BLOCKED
    assert wf.next_step(stages).name == "Spectrum import"


def test_measured_spectrum_keeps_quantification_blocked() -> None:
    c = wf.WorkflowContext(
        spectra_loaded=True,
        spectrum_selected=True,
        synthetic=False,
        detector_known=True,
        peak_search_done=True,
        fits_done=True,
        calibration_provisional=True,
    )
    stages = wf.stages(c)
    assert by_name(stages, "Energy calibration").detail.startswith("PROVISIONAL")
    for name in ("Efficiency calculation", "Activity calculation", "Nuclide identification"):
        assert by_name(stages, name).status is wf.StageStatus.BLOCKED
    assert "certified source data" in by_name(stages, "Efficiency calculation").detail
    assert by_name(stages, "Integrity verification").status is wf.StageStatus.OPTIONAL


def test_synthetic_path_to_identification() -> None:
    c = wf.WorkflowContext(
        spectra_loaded=True,
        spectrum_selected=True,
        synthetic=True,
        detector_known=True,
        peak_search_done=True,
        fits_done=True,
        calibration_verified=True,
        efficiency_done=True,
        activity_possible=True,
        library_loaded=True,
    )
    stages = wf.stages(c)
    assert by_name(stages, "Activity calculation").status is wf.StageStatus.READY
    assert by_name(stages, "Nuclide identification").status is wf.StageStatus.READY
    assert "never a definite identification" in by_name(stages, "Nuclide identification").detail
    assert wf.next_step(stages).name == "Resolution analysis"
