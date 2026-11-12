"""Guided workflow: the twelve analysis stages and where the current spectrum stands.

Qt-free. The interface fills a ``WorkflowContext`` from its state; ``stages`` returns
each stage with a status and a one-line explanation. Nothing here runs an analysis or
relaxes a gate: a stage is *blocked* with the reason the corresponding page gives
(e.g. experimental efficiency without certified source data).

Statuses: ``done`` (a result exists), ``ready`` (inputs present, not done yet),
``blocked`` (inputs missing; reason given), ``optional``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StageStatus(StrEnum):
    DONE = "done"
    READY = "ready"
    BLOCKED = "blocked"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    spectra_loaded: bool = False
    spectrum_selected: bool = False
    synthetic: bool = False
    integrity_checked: bool = False
    detector_known: bool = False
    background_designated: bool = False
    peak_search_done: bool = False
    fits_done: bool = False
    calibration_verified: bool = False
    calibration_provisional: bool = False
    resolution_done: bool = False
    efficiency_done: bool = False
    activity_possible: bool = False  # synthetic with an efficiency curve
    library_loaded: bool = False
    project_open: bool = False


@dataclass(frozen=True, slots=True)
class Stage:
    number: int
    name: str
    page: str  # page title in the navigation
    status: StageStatus
    detail: str


def stages(c: WorkflowContext) -> list[Stage]:
    s = StageStatus
    measured_gate = (
        "measured spectra: needs certified source data, reference dates, emission "
        "probabilities and geometry (not supplied), so NSW refuses it"
    )

    def need_spectrum(done: bool, ready_text: str, done_text: str) -> tuple[StageStatus, str]:
        if not c.spectrum_selected:
            return s.BLOCKED, "select a spectrum in the Spectrum Explorer"
        return (s.DONE, done_text) if done else (s.READY, ready_text)

    out: list[Stage] = []

    def add(name: str, page: str, status: StageStatus, detail: str) -> None:
        out.append(Stage(len(out) + 1, name, page, status, detail))

    add(
        "Spectrum import",
        "Project Dashboard",
        s.DONE if c.spectra_loaded else s.READY,
        "spectra loaded" if c.spectra_loaded else "open an experimental or synthetic folder",
    )
    add(
        "Integrity verification",
        "Project Dashboard",
        s.DONE if c.integrity_checked or c.synthetic else s.OPTIONAL,
        "checked against the SHA-256 manifest"
        if c.integrity_checked
        else ("not needed for synthetic data" if c.synthetic else "Dashboard → Verify"),
    )
    add(
        "Detector identification",
        "Spectrum Explorer",
        *need_spectrum(
            c.detector_known, "check the Detector tab", "detector profile recorded in the file"
        ),
    )
    add(
        "Background review",
        "Background Subtraction",
        s.DONE if c.background_designated else s.OPTIONAL,
        "a background is designated"
        if c.background_designated
        else "designate a background measurement if one exists (never by file name)",
    )
    add(
        "Peak detection",
        "Spectrum Explorer",
        *need_spectrum(c.peak_search_done, "search for peaks", "peak candidates found"),
    )
    add(
        "Peak fitting",
        "Spectrum Explorer",
        *need_spectrum(c.fits_done, "fit regions (R, then F)", "fits available"),
    )
    if c.calibration_verified:
        cal = (s.DONE, "verified energy calibration")
    elif c.calibration_provisional:
        cal = (s.DONE, "PROVISIONAL calibration (not verified): channel axis kept")
    elif not c.fits_done:
        cal = (s.BLOCKED, "fit peaks first")
    else:
        cal = (s.READY, "assign reference energies (Suggest assignments helps)")
    add("Energy calibration", "Energy Calibration", *cal)
    add(
        "Resolution analysis",
        "Resolution Analysis",
        s.DONE if c.resolution_done else (s.READY if c.fits_done else s.BLOCKED),
        "resolution model fitted"
        if c.resolution_done
        else ("fit the widths of several peaks" if c.fits_done else "fit peaks first"),
    )
    if not c.spectrum_selected:
        eff = (s.BLOCKED, "select a spectrum")
    elif not c.synthetic:
        eff = (s.BLOCKED, measured_gate)
    else:
        eff = (
            (s.DONE, "efficiency curve fitted")
            if c.efficiency_done
            else (
                s.READY,
                "fictitious source SYN-CAL",
            )
        )
    add("Efficiency calculation", "Efficiency Calibration", *eff)
    if not c.spectrum_selected:
        act = (s.BLOCKED, "select a spectrum")
    elif not c.synthetic:
        act = (s.BLOCKED, measured_gate)
    elif c.activity_possible:
        act = (s.READY, "fictitious sample SYN-X")
    else:
        act = (s.BLOCKED, "needs an efficiency curve")
    add("Activity calculation", "Activity Calculation", *act)
    if not c.spectrum_selected:
        ident = (s.BLOCKED, "select a spectrum")
    elif not c.calibration_verified:
        ident = (s.BLOCKED, "needs a verified energy calibration")
    elif not c.library_loaded:
        ident = (s.BLOCKED, "load a reference library")
    else:
        ident = (s.READY, "rank nuclide candidates (never a definite identification)")
    add("Nuclide identification", "Nuclide Identification", *ident)
    add(
        "Reporting",
        "Project File",
        s.READY if c.project_open else s.OPTIONAL,
        "save the session and export a PDF report"
        if c.project_open
        else "create a project (File → New project) to keep and report results",
    )
    return out


def next_step(stage_list: list[Stage]) -> Stage | None:
    """The first stage that is ready: the suggested next step."""
    return next((st for st in stage_list if st.status is StageStatus.READY), None)
