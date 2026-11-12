"""Application state shared by the pages (no physics, no file access)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from nsw.core.nuclear_data import ReferenceLibrary
from nsw.core.spectrum import MeasurementStatus, Spectrum
from nsw.services.calibration import CalibrationHistoryItem
from nsw.services.dataset import DatasetScan
from nsw.services.efficiency import EfficiencyCurve
from nsw.services.identification import fictitious_library
from nsw.services.peaks import FitResult, PeakSearchResult
from nsw.services.roi import Roi


@dataclass(frozen=True)
class Entry:
    """One selectable spectrum in the explorer."""

    spectrum: Spectrum
    files: tuple[str, ...]  # all file names holding this channel data
    series_size: int  # number of saves in its acquisition (1 = single save)

    @property
    def key(self) -> str:
        return self.spectrum.data_sha256

    @property
    def title(self) -> str:
        s = self.spectrum
        if s.source is not None:
            return s.source.filename
        return s.synthetic_id or "derived"


def entries_from_scan(scan: DatasetScan) -> list[Entry]:
    out: list[Entry] = []
    for acq in scan.identity.acquisitions:
        for snap in acq.snapshots:
            out.append(Entry(snap.preferred, snap.filenames, len(acq.snapshots)))
    return out


class AppState(QObject):
    """Holds the scans and the analyst's background designations."""

    changed = Signal()
    peaks_changed = Signal(str)  # data hash of the spectrum whose search changed
    fits_changed = Signal(str)  # data hash of the spectrum whose fits changed
    calibrations_changed = Signal()
    efficiency_changed = Signal()
    rois_changed = Signal(str)  # data hash of the spectrum whose regions changed
    libraries_changed = Signal()
    assignments_changed = Signal(str)  # data hash

    def __init__(self) -> None:
        super().__init__()
        self.experimental: DatasetScan | None = None
        self.synthetic: DatasetScan | None = None
        self._backgrounds: dict[str, Spectrum] = {}  # data hash -> BACKGROUND spectrum
        self._peak_searches: dict[str, PeakSearchResult] = {}  # data hash -> latest search
        self._fits: dict[str, list[FitResult]] = {}  # data hash -> fits, by region
        self.calibration_history: list[CalibrationHistoryItem] = []
        self.efficiency: tuple[str, EfficiencyCurve] | None = None  # (spectrum hash, curve)
        self._rois: dict[str, dict[str, Roi]] = {}  # data hash -> region id -> region
        self._excluded: dict[str, frozenset[float]] = {}  # data hash -> channels
        # reference libraries loaded in this session (the fictitious one always)
        self.libraries: list[ReferenceLibrary] = [fictitious_library()]
        self._assignments: dict[str, list[dict[str, object]]] = {}

    # ---- scans
    def set_experimental(self, scan: DatasetScan) -> None:
        self.experimental = scan
        self._backgrounds = {
            k: v for k, v in self._backgrounds.items() if v.is_synthetic
        }  # designations belong to a dataset
        self.changed.emit()

    def set_synthetic(self, scan: DatasetScan) -> None:
        self.synthetic = scan
        self.changed.emit()

    def experimental_entries(self) -> list[Entry]:
        return entries_from_scan(self.experimental) if self.experimental else []

    def synthetic_entries(self) -> list[Entry]:
        return entries_from_scan(self.synthetic) if self.synthetic else []

    def all_entries(self) -> list[Entry]:
        return [*self.experimental_entries(), *self.synthetic_entries()]

    # ---- peak searches (latest per spectrum; shared by the peak pages)
    def set_peak_search(self, result: PeakSearchResult) -> None:
        self._peak_searches[result.spectrum_sha256] = result
        self.peaks_changed.emit(result.spectrum_sha256)

    def peak_search(self, key: str) -> PeakSearchResult | None:
        return self._peak_searches.get(key)

    def excluded_candidates(self, key: str) -> frozenset[float]:
        """Candidate channels the analyst excluded from fitting (rounded keys)."""
        return self._excluded.get(key, frozenset())

    def set_excluded_candidates(self, key: str, channels: frozenset[float]) -> None:
        self._excluded[key] = frozenset(channels)
        self.peaks_changed.emit(key)

    # ---- peak fits (per spectrum). A new fit replaces every earlier fit of the same
    # peak: one whose region is identical, holds a peak of the new fit, or whose peaks
    # lie in the new region. The latest fit is the analyst's result (reports would
    # otherwise list one peak twice). Neighbouring groups whose regions merely overlap
    # at their margins are both kept.
    def store_fit(self, key: str, result: FitResult) -> None:
        def same_peaks(r: FitResult) -> bool:
            if (r.first_channel, r.last_channel) == (result.first_channel, result.last_channel):
                return True
            inside_new = any(
                result.first_channel <= p.centroid <= result.last_channel for p in r.peaks
            )
            inside_old = any(r.first_channel <= p.centroid <= r.last_channel for p in result.peaks)
            return inside_new or inside_old

        kept = [r for r in self._fits.get(key, []) if not same_peaks(r)]
        kept.append(result)
        kept.sort(key=lambda r: r.first_channel)
        self._fits[key] = kept
        self.fits_changed.emit(key)

    def fits(self, key: str) -> list[FitResult]:
        return list(self._fits.get(key, []))

    def clear_fits(self, key: str) -> None:
        if self._fits.pop(key, None) is not None:
            self.fits_changed.emit(key)

    # ---- regions of interest (edited through ui.roi_controller, with undo)
    def rois(self, key: str) -> list[Roi]:
        return list(self._rois.get(key, {}).values())

    def roi(self, key: str, roi_id: str) -> Roi | None:
        return self._rois.get(key, {}).get(roi_id)

    def put_roi(self, roi: Roi) -> None:
        self._rois.setdefault(roi.data_sha256, {})[roi.roi_id] = roi
        self.rois_changed.emit(roi.data_sha256)

    def remove_roi(self, key: str, roi_id: str) -> None:
        if self._rois.get(key, {}).pop(roi_id, None) is not None:
            self.rois_changed.emit(key)

    # ---- reference libraries and line assignments
    def add_library(self, library: ReferenceLibrary) -> None:
        self.libraries.append(library)
        self.libraries_changed.emit()

    def record_assignment(self, key: str, decision: dict[str, object]) -> None:
        """Append an analyst's accept / reject decision on a line assignment."""
        self._assignments.setdefault(key, []).append(dict(decision))
        self.assignments_changed.emit(key)

    def assignments(self, key: str) -> list[dict[str, object]]:
        return list(self._assignments.get(key, []))

    def entry(self, key: str) -> Entry | None:
        return next((e for e in self.all_entries() if e.key == key), None)

    # ---- calibration history (session; persisted from Phase 11)
    def add_calibration(self, item: CalibrationHistoryItem) -> None:
        self.calibration_history.append(item)
        self.calibrations_changed.emit()

    # ---- efficiency curve (latest; used by the activity page)
    def set_efficiency(self, key: str, curve: EfficiencyCurve) -> None:
        self.efficiency = (key, curve)
        self.efficiency_changed.emit()

    # ---- background designation (analyst decision, never inferred from file names)
    def designate_background(self, spectrum: Spectrum, note: str = "designated in NSW") -> None:
        if spectrum.is_synthetic:
            raise ValueError("synthetic backgrounds are defined by their generator role")
        if spectrum.measurement_status is not MeasurementStatus.INDEPENDENT:
            raise ValueError(
                "only an independent measurement can be designated as background "
                f"(this one is: {spectrum.measurement_status.value})"
            )
        self._backgrounds[spectrum.data_sha256] = spectrum.designated_as_background(note)
        self.changed.emit()

    def remove_background(self, key: str) -> None:
        if self._backgrounds.pop(key, None) is not None:
            self.changed.emit()

    def is_background(self, key: str) -> bool:
        return key in self._backgrounds

    def background_entries(self) -> list[Entry]:
        by_key = {e.key: e for e in self.experimental_entries()}
        out = [
            Entry(bg, by_key[k].files if k in by_key else (), 1)
            for k, bg in self._backgrounds.items()
        ]
        # synthetic spectra generated in the background role
        out.extend(e for e in self.synthetic_entries() if e.spectrum.kind.value == "background")
        return out
