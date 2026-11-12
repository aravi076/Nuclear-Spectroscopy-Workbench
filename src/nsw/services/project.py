"""Use case: NSW project files (create, open, save results, reopen, back up).

A project is one SQLite file (``nsw.database.store``). This module turns analysis
objects into the JSON records the store keeps and back:

* spectra are stored completely and restored bit for bit (hash checked);
* background designations, energy calibrations (validated records) and peak fits are
  restored as working objects: a stored fit is reproduced by fitting the restored
  spectrum again with the stored settings, and the result must agree with the stored
  values, otherwise the reopening reports a reproducibility failure;
* every other result (peak searches, resolution, identification, efficiency, activity,
  validation runs) is kept as a record for history and reports.

Project files are never written inside an experimental dataset folder.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import nsw
from nsw.analysis.energy_calibration import CalibrationFit, fit_energy_calibration
from nsw.analysis.fitting import FitResult, FitSettings, fit_peaks, fit_peaks_joint
from nsw.analysis.identification import IdentificationResult
from nsw.analysis.peaks import PeakSearchResult
from nsw.analysis.resolution import ResolutionFit
from nsw.core.calibration import CalibrationStatus, EnergyCalibration
from nsw.core.errors import ProcessingError
from nsw.core.nuclear_data import ReferenceLibrary
from nsw.core.spectrum import Spectrum, SpectrumKind
from nsw.database.store import ProjectStore, ProjectStoreError, StoredRun, canonical_digest
from nsw.io.identity import analyse_identity
from nsw.io.reference_data import library_document
from nsw.services import roi as roi_service
from nsw.services.calibration import CalibrationHistoryItem
from nsw.services.dataset import DatasetScan
from nsw.services.export import ExportError, ensure_output_allowed
from nsw.services.peaks import spectrum_name
from nsw.services.roi import Roi

__all__ = [
    "PROJECT_SUFFIX",
    "ProjectContents",
    "ProjectStore",
    "ProjectStoreError",
    "SaveReport",
    "SessionCalibration",
    "SessionSpectrum",
    "StoredRun",
    "create_project",
    "history_table",
    "load_project",
    "open_project",
    "save_calibration",
    "save_fits",
    "save_identification",
    "save_peak_search",
    "save_record",
    "save_reference_library",
    "save_resolution",
    "save_session",
    "save_spectrum",
    "session_scans",
]

PROJECT_SUFFIX = ".nswproj"
REPRODUCTION_RTOL = 1e-6  # relative agreement required when a stored fit is re-run
RESTART_FRACTION = 1e-3  # without stored starting values: |difference| <= this x uncertainty
Table = tuple[list[str], list[list[str]]]


# Version of the algorithm behind each kind of stored result. Change it whenever a code
# change can alter that result; reopened results with another version are compared with
# a tolerance and reported (docs/design/SCIENTIFIC_PROVENANCE_DESIGN.md §3).
ALGORITHM_VERSIONS = {
    "peak-fit": "fitting/2",  # 2: starting values recorded for exact reproduction
    "peak-search": "peaks/1",
    "identification": "identification/1",
    "resolution": "resolution/1",
}


def _provenance(kind: str, result: Mapping[str, Any]) -> dict[str, Any]:
    import platform

    import numpy
    import scipy

    return {
        "algorithm": ALGORITHM_VERSIONS.get(kind, f"{kind}/1"),
        "software": {
            "nsw": nsw.__version__,
            "python": platform.python_version(),
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
        },
        "result_sha256": canonical_digest(dict(result)),
    }


def _add_run(
    store: ProjectStore,
    kind: str,
    *,
    data_sha256: str | None,
    synthetic: bool,
    label: str,
    settings: Mapping[str, Any],
    result: Mapping[str, Any],
    software: str,
) -> int:
    """``store.add_run`` with a provenance block (algorithm, versions, result digest)."""
    with_prov = {**dict(settings), "provenance": _provenance(kind, result)}
    return store.add_run(
        kind,
        data_sha256=data_sha256,
        synthetic=synthetic,
        label=label,
        settings=with_prov,
        result=result,
        software=software,
    )


def verify_digests(runs: Iterable[StoredRun]) -> tuple[int, list[str]]:
    """(number of results whose digest matches, problems). Runs saved before digests
    existed are counted as not covered, not as failures."""
    ok, problems = 0, []
    for run in runs:
        prov = run.settings.get("provenance")
        if not isinstance(prov, Mapping) or "result_sha256" not in prov:
            continue
        if prov["result_sha256"] == canonical_digest(run.result):
            ok += 1
        else:
            problems.append(f"{run.kind} {run.id}: stored result does not match its digest")
    return ok, problems


def _software() -> str:
    return nsw.__version__


def _label(spectrum: Spectrum) -> str:
    if spectrum.is_synthetic:
        return "SYNTHETIC"
    if spectrum.calibration_status is not CalibrationStatus.VERIFIED:
        return "EXPERIMENTAL, PROVISIONAL"
    return "EXPERIMENTAL"


# ---------------------------------------------------------------------- lifecycle
def create_project(
    path: Path, name: str, protected_roots: Iterable[Path] = (), description: str = ""
) -> ProjectStore:
    path = Path(path)
    if path.suffix.lower() != PROJECT_SUFFIX:
        path = path.with_name(path.name + PROJECT_SUFFIX)
    try:
        target = ensure_output_allowed(path, protected_roots, overwrite=False)
    except ExportError as exc:
        raise ProjectStoreError(str(exc)) from exc
    return ProjectStore.create(target, name, _software(), description)


def open_project(path: Path) -> ProjectStore:
    return ProjectStore.open(Path(path))


def save_spectrum(store: ProjectStore, spectrum: Spectrum) -> bool:
    return store.add_spectrum(spectrum, spectrum_name(spectrum))


def _ensure_spectrum(store: ProjectStore, spectrum: Spectrum) -> None:
    save_spectrum(store, spectrum)


# ---------------------------------------------------------------------- records
def _settings_dict(settings: FitSettings) -> dict[str, Any]:
    return {
        "method": settings.method.value,
        "background": settings.background.value,
        "shared_width": settings.shared_width,
        "step": settings.step,
        "max_iterations": settings.max_iterations,
        "tolerance": settings.tolerance,
        "fixed_centroids": settings.fixed_centroids,
        "fixed_width": settings.fixed_width,
    }


def fit_record(result: FitResult) -> dict[str, Any]:
    return {
        "method": result.method,
        "first_channel": result.first_channel,
        "last_channel": result.last_channel,
        "parameter_names": list(result.parameter_names),
        "values": [float(v) for v in result.values],
        "peaks": [
            {
                "centroid": p.centroid,
                "centroid_unc": p.centroid_unc,
                "area": p.area,
                "area_unc": p.area_unc,
                "sigma": p.sigma,
                "sigma_unc": p.sigma_unc,
                "fwhm": p.fwhm,
                "fwhm_unc": p.fwhm_unc,
                "energy_kev": p.energy_kev,
                "at_bound": list(p.at_bound),
            }
            for p in result.peaks
        ],
        "background": [list(b) for b in result.background],
        "statistic": result.statistic,
        "statistic_name": result.statistic_name,
        "ndf": result.ndf,
        "p_value": result.p_value,
        "gof_method": result.gof_method,
        "converged": result.converged,
        "ok": result.ok,
        "poor_fit": result.poor_fit,
        "flags": list(result.flags),
        "warnings": list(result.warnings),
    }


def save_fits(
    store: ProjectStore,
    spectrum: Spectrum,
    fits: Sequence[FitResult],
    background: Spectrum | None = None,
) -> list[int]:
    """Store fits of ``spectrum`` with everything needed to reproduce them."""
    _ensure_spectrum(store, spectrum)
    if background is not None:
        _ensure_spectrum(store, background)
    ids = []
    for r in fits:
        if r.spectrum_sha256 != spectrum.data_sha256:
            raise ProcessingError("a fit belongs to a different spectrum")
        joint = r.method == "joint-poisson"
        if joint and background is None:
            raise ProcessingError("a joint fit needs its background spectrum to be stored")
        settings = {
            "first_channel": r.first_channel,
            "last_channel": r.last_channel,
            # the exact starting values when known; else the fitted values (older fits)
            "centroids": list(r.initial_centroids) or [p.centroid for p in r.peaks],
            "start_fwhm": list(r.initial_fwhm) or [p.fwhm for p in r.peaks],
            "exact_start": bool(r.initial_centroids),
            "fit_settings": _settings_dict(r.settings),
            "background_sha256": background.data_sha256 if joint and background else None,
        }
        ids.append(
            _add_run(
                store,
                "peak-fit",
                data_sha256=spectrum.data_sha256,
                synthetic=spectrum.is_synthetic,
                label=_label(spectrum),
                settings=settings,
                result=fit_record(r),
                software=_software(),
            )
        )
    return ids


def save_peak_search(store: ProjectStore, spectrum: Spectrum, result: PeakSearchResult) -> int:
    _ensure_spectrum(store, spectrum)
    s = result.settings
    return _add_run(
        store,
        "peak-search",
        data_sha256=spectrum.data_sha256,
        synthetic=spectrum.is_synthetic,
        label=_label(spectrum),
        settings={
            "fwhm_channels": list(s.fwhm_channels),
            "threshold": s.threshold,
            "min_separation_fwhm": s.min_separation_fwhm,
            "overlap_fwhm": s.overlap_fwhm,
            "first_channel": result.first_channel,
            "last_channel": result.last_channel,
        },
        result=_search_result(result),
        software=_software(),
    )


def save_calibration(
    store: ProjectStore, spectrum: Spectrum, record: EnergyCalibration, summary: Mapping[str, Any]
) -> int:
    _ensure_spectrum(store, spectrum)
    return store.add_calibration(
        "energy",
        record.model_dump(mode="json"),
        data_sha256=spectrum.data_sha256,
        status=record.status.value,
        synthetic=spectrum.is_synthetic,
        software=_software(),
        summary=summary,
    )


def save_resolution(store: ProjectStore, spectrum: Spectrum, fit: ResolutionFit) -> int:
    _ensure_spectrum(store, spectrum)
    return store.add_calibration(
        "resolution",
        {
            "variable": fit.variable.value,
            "coefficients": [float(c) for c in fit.coefficients],
            "covariance": [[float(v) for v in row] for row in fit.covariance],
            "points": [[p.x, p.fwhm, p.fwhm_unc, p.label] for p in fit.points],
        },
        data_sha256=spectrum.data_sha256,
        status="synthetic" if spectrum.is_synthetic else "provisional",
        synthetic=spectrum.is_synthetic,
        software=_software(),
        summary={
            "chi2": fit.chi2,
            "ndf": fit.ndf,
            "p_value": fit.p_value,
            "warnings": list(fit.warnings),
        },
    )


def save_identification(
    store: ProjectStore, spectrum: Spectrum, result: IdentificationResult, library_name: str
) -> int:
    _ensure_spectrum(store, spectrum)
    s = result.settings
    return _add_run(
        store,
        "identification",
        data_sha256=spectrum.data_sha256,
        synthetic=spectrum.is_synthetic,
        label=_label(spectrum),
        settings={
            "library": library_name,
            "k": s.k,
            "min_emission_probability": s.min_emission_probability,
            "strong_ratio": s.strong_ratio,
            "missing_factor": s.missing_factor,
            "intensity_p_min": s.intensity_p_min,
            "extra_energy_unc_kev": s.extra_energy_unc_kev,
            "energy_range_kev": list(result.energy_range_kev),
            "efficiency_curve": result.with_efficiency,
        },
        result={
            "candidates": [
                {
                    "nuclide": c.nuclide,
                    "evaluation": c.evaluation,
                    "dataset_id": c.dataset_id,
                    "class": c.confidence.value,
                    "score": c.score,
                    "matched_lines_kev": [m.line.energy_kev for m in c.matches],
                    "missing_lines_kev": [u.line.energy_kev for u in c.missing],
                    "reasons": list(c.reasons),
                }
                for c in result.candidates
            ],
            "unexplained_peaks_kev": [result.peaks[i].energy_kev for i in result.unexplained_peaks],
            "warnings": list(result.warnings),
        },
        software=_software(),
    )


def save_reference_library(store: ProjectStore, library: ReferenceLibrary) -> int:
    document = library_document(library)
    digest = hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return store.add_reference_dataset(
        library.name, library.evaluations, library.fictitious, document, digest
    )


def save_record(
    store: ProjectStore,
    kind: str,
    spectrum: Spectrum | None,
    settings: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    synthetic: bool | None = None,
) -> int:
    """Any other result (efficiency, activity, validation run) as a history record."""
    if spectrum is not None:
        _ensure_spectrum(store, spectrum)
    is_synthetic = spectrum.is_synthetic if spectrum is not None else bool(synthetic)
    return _add_run(
        store,
        kind,
        data_sha256=spectrum.data_sha256 if spectrum is not None else None,
        synthetic=is_synthetic,
        label=_label(spectrum) if spectrum is not None else ("SYNTHETIC" if is_synthetic else ""),
        settings=settings,
        result=result,
        software=_software(),
    )


# ---------------------------------------------------------------------- reopening
@dataclass
class ProjectContents:
    info: dict[str, Any]
    spectra: dict[str, Spectrum]  # data hash -> spectrum (backgrounds re-designated)
    names: dict[str, str]
    backgrounds: dict[str, str]  # data hash -> note
    fits: dict[str, list[FitResult]] = field(default_factory=dict)
    calibrations: list[tuple[str | None, EnergyCalibration]] = field(default_factory=list)
    calibration_items: list[CalibrationHistoryItem] = field(default_factory=list)
    history: list[StoredRun] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # reproducibility failures etc.
    rois: dict[str, tuple[Roi, ...]] = field(default_factory=dict)
    excluded: dict[str, frozenset[float]] = field(default_factory=dict)
    assignments: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    digests_verified: int = 0


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    if math.isnan(a) and math.isnan(b):
        return True
    return math.isclose(a, b, rel_tol=REPRODUCTION_RTOL, abs_tol=1e-9)


def reproduce_calibration(record: EnergyCalibration) -> CalibrationFit:
    """Fit the stored reference points again; the coefficients must agree."""
    inflated = "multiplied by chi2/ndf" in record.method
    fit = fit_energy_calibration(record.points, len(record.coefficients) - 1, inflate=inflated)
    for old, new in zip(record.coefficients, fit.coefficients, strict=True):
        if not _close(old, float(new)):
            raise ProcessingError(f"coefficient {old!r} is now {float(new)!r}")
    return fit


def reproduce_fit(run: StoredRun, spectra: Mapping[str, Spectrum]) -> FitResult:
    """Fit the stored spectrum again with the stored settings; raise if the result does
    not agree with the stored one."""
    s = run.settings
    spectrum = spectra[run.data_sha256 or ""]
    settings = FitSettings(**s["fit_settings"])
    if s["background_sha256"]:
        result = fit_peaks_joint(
            spectrum,
            spectra[s["background_sha256"]],
            s["first_channel"],
            s["last_channel"],
            s["centroids"],
            s["start_fwhm"],
            settings,
        )
    else:
        result = fit_peaks(
            spectrum,
            s["first_channel"],
            s["last_channel"],
            s["centroids"],
            s["start_fwhm"],
            settings,
        )
    stored = run.result["peaks"]
    if len(stored) != len(result.peaks):
        raise ProcessingError("re-run fit has a different number of peaks")
    exact = bool(s.get("exact_start"))
    for old, new in zip(stored, result.peaks, strict=True):
        for key, unc in (
            ("centroid", "centroid_unc"),
            ("area", "area_unc"),
            ("sigma", "sigma_unc"),
        ):
            a, b = old[key], getattr(new, key)
            if exact:
                agree = _close(a, b)
            else:  # restarted from the optimum: agree to a small fraction of the uncertainty
                u = old[unc]
                agree = _close(a, b) or (
                    u is not None and math.isfinite(u) and abs(a - b) <= RESTART_FRACTION * u
                )
            if not agree:
                raise ProcessingError(
                    f"re-run fit does not reproduce {key}: stored {a!r}, now {b!r}"
                )
    return result


def load_project(store: ProjectStore, *, reproduce_fits: bool = True) -> ProjectContents:
    names: dict[str, str] = {}
    spectra: dict[str, Spectrum] = {}
    for sha, name, _, _ in store.spectrum_names():
        spectra[sha] = store.spectrum(sha)
        names[sha] = name
    backgrounds = store.backgrounds()
    contents = ProjectContents(
        info=store.info, spectra=spectra, names=names, backgrounds=backgrounds
    )
    for sha, note in backgrounds.items():
        if sha in spectra:
            spectra[sha] = spectra[sha].designated_as_background(note)
    for cal in store.calibrations("energy"):
        try:
            record = EnergyCalibration.model_validate(cal.record)
        except ValueError as exc:
            contents.problems.append(f"calibration {cal.id} could not be restored: {exc}")
            continue
        contents.calibrations.append((cal.data_sha256, record))
        if not record.points:
            continue
        try:
            fit = reproduce_calibration(record)
        except ProcessingError as exc:
            contents.problems.append(f"calibration {cal.id} not reproduced: {exc}")
            continue
        contents.calibration_items.append(
            CalibrationHistoryItem(
                datetime.fromisoformat(cal.created),
                names.get(cal.data_sha256 or "", "—"),
                cal.synthetic,
                record,
                fit,
                cal.data_sha256 or "",
            )
        )
    contents.history = store.runs()
    contents.digests_verified, digest_problems = verify_digests(contents.history)
    contents.problems.extend(digest_problems)
    for sha in spectra:
        rois = roi_service.current_rois(store, sha)
        if rois:
            contents.rois[sha] = rois
        excluded = store.decisions(sha, subject_prefix=EXCLUDED_SUBJECT)
        if excluded:
            channels = excluded[-1].value.get("channels", [])
            contents.excluded[sha] = frozenset(float(c) for c in channels)
        decided = store.decisions(sha, subject_prefix="assignment:")
        if decided:
            contents.assignments[sha] = [dict(d.value) for d in decided]
    if reproduce_fits:
        originals = {sha: store.spectrum(sha) for sha in spectra}
        for run in store.runs("peak-fit"):
            try:
                result = reproduce_fit(run, originals)
            except (ProcessingError, KeyError, TypeError, ValueError) as exc:
                contents.problems.append(f"fit {run.id} not reproduced: {exc}")
                continue
            contents.fits.setdefault(run.data_sha256 or "", []).append(result)
    return contents


def history_table(runs: Sequence[StoredRun], names: Mapping[str, str]) -> Table:
    headers = ["#", "Created (UTC)", "Analysis", "Spectrum", "Data", "Summary", "Software"]
    rows = []
    for r in runs:
        rows.append(
            [
                str(r.id),
                r.created.replace("T", " ").replace("+00:00", ""),
                r.kind,
                names.get(r.data_sha256 or "", "—"),
                r.label or ("synthetic" if r.synthetic else "—"),
                _summary(r),
                r.software,
            ]
        )
    return headers, rows


def _summary(run: StoredRun) -> str:
    res = run.result
    if run.kind == "peak-fit":
        peaks = res.get("peaks", [])
        text = ", ".join(f"{p['centroid']:.2f} ch ({p['area']:.4g})" for p in peaks)
        return text + ("" if res.get("ok") else " — not ok")
    if run.kind == "peak-search":
        return f"{len(res.get('candidates', []))} candidates"
    if run.kind == "identification":
        cands = res.get("candidates", [])
        return ", ".join(f"{c['nuclide']} ({c['class']})" for c in cands[:4]) or "no candidates"
    return ", ".join(
        f"{k}: {v}" for k, v in list(res.items())[:3] if not isinstance(v, (list, dict))
    )


# ---------------------------------------------------------------------- sessions
@dataclass(frozen=True)
class SessionSpectrum:
    """What the interface holds for one spectrum."""

    spectrum: Spectrum
    search: PeakSearchResult | None = None
    fits: tuple[FitResult, ...] = ()
    rois: tuple[Roi, ...] = ()
    excluded: frozenset[float] = frozenset()
    assignments: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class SessionCalibration:
    spectrum: Spectrum | None
    record: EnergyCalibration
    summary: Mapping[str, Any]


@dataclass(frozen=True)
class SaveReport:
    spectra: int
    searches: int
    fits: int
    calibrations: int
    backgrounds: int
    skipped: tuple[str, ...]
    decisions: int = 0

    def text(self) -> str:
        parts = [
            f"{self.spectra} new spectra",
            f"{self.searches} peak searches",
            f"{self.fits} fits",
            f"{self.calibrations} calibrations",
            f"{self.backgrounds} background designations",
            f"{self.decisions} analyst decisions",
        ]
        text = "Saved " + ", ".join(parts) + " (results already in the project are not repeated)."
        if self.skipped:
            text += " Not saved: " + "; ".join(self.skipped)
        return text


def _fingerprint(kind: str, sha: str | None, payload: Mapping[str, Any]) -> str:
    text = json.dumps([kind, sha, payload], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_session(
    store: ProjectStore,
    items: Sequence[SessionSpectrum],
    calibrations: Sequence[SessionCalibration] = (),
    backgrounds: Sequence[Spectrum] = (),
) -> SaveReport:
    """Save what the session holds. Results identical to stored ones are not added again
    (the store is append-only, so repeated saving must not multiply rows)."""
    existing = {_fingerprint(r.kind, r.data_sha256, r.result) for r in store.runs()} | {
        _fingerprint("energy", c.data_sha256, c.record) for c in store.calibrations("energy")
    }
    new_spectra = searches = n_fits = n_cal = n_bg = n_decisions = 0
    skipped: list[str] = []
    for item in items:
        s = item.spectrum
        new_spectra += save_spectrum(store, s)
        if item.search is not None:
            stored = json.loads(json.dumps(_search_result(item.search), default=str))
            if _fingerprint("peak-search", s.data_sha256, stored) not in existing:
                save_peak_search(store, s, item.search)
                searches += 1
        for fit in item.fits:
            if fit.method == "joint-poisson":
                skipped.append(
                    f"joint fit of {spectrum_name(s)} channels {fit.first_channel}–"
                    f"{fit.last_channel} (its background is not recorded with the fit)"
                )
                continue
            record = json.loads(json.dumps(fit_record(fit), default=str))
            if _fingerprint("peak-fit", s.data_sha256, record) in existing:
                continue
            save_fits(store, s, [fit])
            n_fits += 1
        n_decisions += _save_decisions(store, item)
    for cal in calibrations:
        record = cal.record.model_dump(mode="json")
        sha = cal.spectrum.data_sha256 if cal.spectrum is not None else None
        if _fingerprint("energy", sha, record) in existing:
            continue
        if cal.spectrum is None:
            store.add_calibration(
                "energy",
                record,
                data_sha256=None,
                status=cal.record.status.value,
                synthetic=False,
                software=_software(),
                summary=cal.summary,
            )
        else:
            save_calibration(store, cal.spectrum, cal.record, cal.summary)
        n_cal += 1
    current = store.backgrounds()
    for bg in backgrounds:
        if bg.data_sha256 in current:
            continue
        note = bg.provenance[-1].note if bg.provenance else ""
        if (
            bg.kind is SpectrumKind.BACKGROUND
            and bg.provenance
            and bg.provenance[-1].operation == "designate-background"
        ):
            bg = dataclasses.replace(bg, kind=SpectrumKind.RAW, provenance=bg.provenance[:-1])
        save_spectrum(store, bg)
        store.set_background(bg.data_sha256, True, note)
        n_bg += 1
    store.log("save-session", f"{new_spectra} spectra, {n_fits} fits, {n_cal} calibrations")
    return SaveReport(new_spectra, searches, n_fits, n_cal, n_bg, tuple(skipped), n_decisions)


EXCLUDED_SUBJECT = "candidates:excluded"


def _save_decisions(store: ProjectStore, item: SessionSpectrum) -> int:
    """Append the analyst decisions of one spectrum that the project does not hold yet:
    region changes (against the replayed regions), the set of excluded candidates (when
    it changed) and line-assignment decisions (each once)."""
    if not (item.rois or item.excluded or item.assignments):
        stored_rois = roi_service.current_rois(store, item.spectrum.data_sha256)
        if not stored_rois and not store.decisions(item.spectrum.data_sha256, EXCLUDED_SUBJECT):
            return 0
    sha = item.spectrum.data_sha256
    save_spectrum(store, item.spectrum)
    n = 0
    stored = {r.roi_id: r for r in roi_service.current_rois(store, sha)}
    current = {r.roi_id: r for r in item.rois}
    for rid, roi in current.items():
        if rid not in stored:
            roi_service.record(store, roi, "roi_create")
            n += 1
        elif stored[rid] != roi:
            roi_service.record(store, roi, "roi_edit")
            n += 1
    for rid, roi in stored.items():
        if rid not in current:
            roi_service.record(store, roi, "roi_delete")
            n += 1
    previous = store.decisions(sha, subject_prefix=EXCLUDED_SUBJECT)
    last = (
        frozenset(float(c) for c in previous[-1].value.get("channels", []))
        if previous
        else frozenset()
    )
    if item.excluded != last:
        store.add_decision(
            EXCLUDED_SUBJECT,
            "candidates_excluded",
            data_sha256=sha,
            value={"channels": sorted(item.excluded)},
        )
        n += 1
    known = {canonical_digest(d.value) for d in store.decisions(sha, subject_prefix="assignment:")}
    for decision in item.assignments:
        value = dict(decision)
        if canonical_digest(value) in known:
            continue
        subject = f"assignment:{value.get('nuclide', '?')}:{value.get('line_kev', '?')}"
        action = "assignment_accept" if value.get("accepted") else "assignment_reject"
        store.add_decision(subject, action, data_sha256=sha, value=value)
        known.add(canonical_digest(value))
        n += 1
    return n


def _search_result(result: PeakSearchResult) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "channel": c.channel,
                "channel_uncertainty": c.channel_uncertainty,
                "significance": c.significance,
                "area_estimate": c.area_estimate,
                "group": c.group,
            }
            for c in result.candidates
        ],
        "warnings": list(result.warnings),
    }


def session_scans(contents: ProjectContents, path: Path) -> tuple[DatasetScan, DatasetScan]:
    """(experimental, synthetic) scans of the stored spectra, for loading into the
    interface. Background designations are restored separately."""
    experimental: list[Spectrum] = []
    synthetic: list[Spectrum] = []
    for sha, spectrum in contents.spectra.items():
        original = (
            spectrum
            if sha not in contents.backgrounds
            else dataclasses.replace(
                spectrum, kind=SpectrumKind.RAW, provenance=spectrum.provenance[:-1]
            )
        )
        (synthetic if original.is_synthetic else experimental).append(original)
    return (
        DatasetScan(path, tuple(experimental), (), analyse_identity(experimental)),
        DatasetScan(path, tuple(synthetic), (), analyse_identity(synthetic)),
    )
