"""Connects a SpectrumPlot to the regions of interest held in AppState, with undo/redo.

The plot reports gestures (region drawn, edges moved, delete requested); this controller
validates them through ``nsw.services.roi``, applies them as undoable commands, and
redraws the plot. Regions belong to one spectrum (the first one shown in the plot).
Rejected gestures (e.g. a region outside the spectrum) are reported through
``message`` and leave the state unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum
from nsw.services import candidates as cand
from nsw.services import roi as roi_service
from nsw.services.peaks import FitResult, FitSettings, PeakSearchResult
from nsw.services.roi import ModelComparison, Roi
from nsw.ui.plot import SpectrumPlot
from nsw.ui.state import AppState


class _PutRoi(QUndoCommand):
    """Create or change a region (``before`` None = create)."""

    def __init__(self, state: AppState, after: Roi, before: Roi | None, text: str) -> None:
        super().__init__(text)
        self.state, self.after, self.before = state, after, before

    def redo(self) -> None:
        self.state.put_roi(self.after)

    def undo(self) -> None:
        if self.before is None:
            self.state.remove_roi(self.after.data_sha256, self.after.roi_id)
        else:
            self.state.put_roi(self.before)


class _DeleteRoi(QUndoCommand):
    def __init__(self, state: AppState, roi: Roi) -> None:
        super().__init__(f"delete region {roi.first_channel}–{roi.last_channel}")
        self.state, self.roi = state, roi

    def redo(self) -> None:
        self.state.remove_roi(self.roi.data_sha256, self.roi.roi_id)

    def undo(self) -> None:
        self.state.put_roi(self.roi)


class _SetExcluded(QUndoCommand):
    def __init__(
        self, state: AppState, key: str, after: frozenset[float], before: frozenset[float]
    ) -> None:
        super().__init__("exclude / restore peak candidates")
        self.state, self.key, self.after, self.before = state, key, after, before

    def redo(self) -> None:
        self.state.set_excluded_candidates(self.key, self.after)

    def undo(self) -> None:
        self.state.set_excluded_candidates(self.key, self.before)


class RoiController(QObject):
    """Region editing for one plot; one undo history per spectrum."""

    message = Signal(str)  # refusals and hints for the status line
    selection_changed = Signal(str)  # selected region id ("" = none)
    fitted = Signal(object)  # FitResult of the selected region
    compared = Signal(object)  # ModelComparison of the selected region

    def __init__(self, state: AppState, plot: SpectrumPlot, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.plot = plot
        self.spectrum: Spectrum | None = None
        self.selected = ""
        self._stacks: dict[str, QUndoStack] = {}
        plot.region_drawn.connect(self.create)
        plot.region_edited.connect(self.edit)
        plot.region_selected.connect(self.select)
        plot.delete_requested.connect(self.delete_selected)
        plot.undo_requested.connect(self.undo)
        plot.redo_requested.connect(self.redo)
        plot.fit_requested.connect(self.fit_selected)
        plot.candidate_add_requested.connect(self.add_candidate)
        self.fit_settings = FitSettings()
        state.rois_changed.connect(self._on_rois_changed)

    # ------------------------------------------------------------------ context
    def set_spectrum(self, spectrum: Spectrum | None) -> None:
        self.spectrum = spectrum
        self.selected = ""
        self._redraw()
        self.selection_changed.emit("")

    @property
    def stack(self) -> QUndoStack | None:
        if self.spectrum is None:
            return None
        key = self.spectrum.data_sha256
        if key not in self._stacks:
            self._stacks[key] = QUndoStack(self)
        return self._stacks[key]

    def rois(self) -> list[Roi]:
        return [] if self.spectrum is None else self.state.rois(self.spectrum.data_sha256)

    def selected_roi(self) -> Roi | None:
        if self.spectrum is None or not self.selected:
            return None
        return self.state.roi(self.spectrum.data_sha256, self.selected)

    # ------------------------------------------------------------------ gestures
    def create(self, first: int, last: int) -> Roi | None:
        stack = self.stack
        if self.spectrum is None or stack is None:
            self.message.emit("Select one spectrum before drawing a region.")
            return None
        try:
            roi = roi_service.make_roi(self.spectrum, first, last)
        except ProcessingError as exc:
            self.message.emit(str(exc))
            return None
        stack.push(_PutRoi(self.state, roi, None, f"create region {first}–{last}"))
        self.select(roi.roi_id)
        return roi

    def edit(self, roi_id: str, first: int, last: int) -> None:
        stack = self.stack
        if self.spectrum is None or stack is None:
            return
        before = self.state.roi(self.spectrum.data_sha256, roi_id)
        if before is None or (before.first_channel, before.last_channel) == (first, last):
            self._redraw()
            return
        lo, hi = sorted((first, last))
        after = before.moved_to(lo, hi)
        problems = roi_service.validate_roi(after, self.spectrum)
        if problems:
            self.message.emit("Region change refused: " + "; ".join(problems))
            self._redraw()  # put the drawn region back where it was
            return
        stack.push(_PutRoi(self.state, after, before, f"move region to {lo}–{hi}"))
        self.select(roi_id)

    def delete_selected(self) -> None:
        roi, stack = self.selected_roi(), self.stack
        if roi is None or stack is None:
            self.message.emit("No region selected.")
            return
        stack.push(_DeleteRoi(self.state, roi))
        self.select("")

    def add_candidate(self, channel: float) -> None:
        """Add a peak at ``channel`` to the region containing it (undoable)."""
        stack = self.stack
        if self.spectrum is None or stack is None:
            return
        rid = self.plot.region_at(channel) or self.selected
        before = self.state.roi(self.spectrum.data_sha256, rid) if rid else None
        if before is None:
            self.message.emit("Draw or select a region first; peaks are added to a region.")
            return
        try:
            after = roi_service.add_peak(before, self.spectrum, channel)
        except ProcessingError as exc:
            self.message.emit(str(exc))
            return
        stack.push(_PutRoi(self.state, after, before, f"add peak at channel {channel:g}"))
        self.select(after.roi_id)
        self.message.emit(f"Peak added at channel {channel:g}.")

    def _region_with_peaks(self) -> tuple[Roi, str] | None:
        roi, stack = self.selected_roi(), self.stack
        if roi is None or stack is None or self.spectrum is None:
            self.message.emit("Select a region to fit (click it, or draw one with R).")
            return None
        search = self.usable_search()
        try:
            with_peaks, source = roi_service.peaks_for_roi(self.spectrum, roi, search)
        except ProcessingError as exc:
            self.message.emit(str(exc))
            return None
        if with_peaks != roi:
            stack.push(_PutRoi(self.state, with_peaks, roi, "peaks for the region"))
        return with_peaks, source

    def fit_selected(self) -> FitResult | None:
        """Fit the selected region; the result joins the spectrum's fits in AppState."""
        prepared = self._region_with_peaks()
        if prepared is None or self.spectrum is None:
            return None
        roi, source = prepared
        try:
            result = roi_service.fit_roi(self.spectrum, roi, self.fit_settings)
        except ProcessingError as exc:
            self.message.emit(f"Fit refused: {exc}")
            return None
        self.state.store_fit(self.spectrum.data_sha256, result)
        quality = "converged" if result.converged else "did NOT converge"
        self.message.emit(f"Fitted {len(roi.peak_channels)} peak(s) ({source}); the fit {quality}.")
        self.fitted.emit(result)
        return result

    def compare_selected(self) -> ModelComparison | None:
        """Fit the selected region with nested continuum models; keep the preferred fit."""
        prepared = self._region_with_peaks()
        if prepared is None or self.spectrum is None:
            return None
        roi, _ = prepared
        try:
            comparison = roi_service.compare_models(self.spectrum, roi, self.fit_settings)
        except ProcessingError as exc:
            self.message.emit(f"Comparison refused: {exc}")
            return None
        best = next((f for f in comparison.fits if f.name == comparison.preferred), None)
        if best is not None and best.result is not None:
            self.state.store_fit(self.spectrum.data_sha256, best.result)
            self.fitted.emit(best.result)
        self.message.emit(comparison.reason)
        self.compared.emit(comparison)
        return comparison

    # ------------------------------------------------------------------ candidates
    def search(
        self, threshold: float = cand.DEFAULT_THRESHOLD, in_selected_region: bool = False
    ) -> int:
        """Search the spectrum (or the selected region); returns the number of candidates."""
        if self.spectrum is None:
            self.message.emit("Select a spectrum first.")
            return 0
        first = last = None
        if in_selected_region:
            roi = self.selected_roi()
            if roi is None:
                self.message.emit("Select a region to search in.")
                return 0
            first, last = roi.first_channel, roi.last_channel
        try:
            found = cand.auto_search(
                self.spectrum, threshold, first_channel=first, last_channel=last
            )
        except ProcessingError as exc:
            self.message.emit(f"Peak search refused: {exc}")
            return 0
        self.state.set_peak_search(found)
        self.message.emit(cand.summary(self.classified()))
        return len(found.candidates)

    def classified(self) -> tuple[cand.ClassifiedCandidate, ...]:
        if self.spectrum is None:
            return ()
        key = self.spectrum.data_sha256
        found = self.state.peak_search(key)
        if found is None:
            return ()
        return cand.classify(found, excluded=self.state.excluded_candidates(key))

    def usable_search(self) -> PeakSearchResult | None:
        if self.spectrum is None:
            return None
        found = self.state.peak_search(self.spectrum.data_sha256)
        return None if found is None else cand.usable(found, self.classified())

    def exclude_selected_markers(self) -> None:
        stack = self.stack
        chosen = self.plot.selected_markers()
        if self.spectrum is None or stack is None or not chosen:
            self.message.emit("Select candidates first (S, then click; Ctrl+click for more).")
            return
        key = self.spectrum.data_sha256
        before = self.state.excluded_candidates(key)
        after = before | {cand.channel_key(c) for c in chosen}
        stack.push(_SetExcluded(self.state, key, after, before))
        self.message.emit(f"{len(chosen)} candidate(s) excluded from fitting.")

    def restore_excluded(self) -> None:
        stack = self.stack
        if self.spectrum is None or stack is None:
            return
        key = self.spectrum.data_sha256
        before = self.state.excluded_candidates(key)
        if before:
            stack.push(_SetExcluded(self.state, key, frozenset(), before))
            self.message.emit("Excluded candidates restored.")

    def select(self, roi_id: str) -> None:
        self.selected = roi_id
        self._redraw()
        self.selection_changed.emit(roi_id)

    def undo(self) -> None:
        if self.stack is not None and self.stack.canUndo():
            self.stack.undo()

    def redo(self) -> None:
        if self.stack is not None and self.stack.canRedo():
            self.stack.redo()

    # ------------------------------------------------------------------ drawing
    def _on_rois_changed(self, key: str) -> None:
        if self.spectrum is not None and key == self.spectrum.data_sha256:
            if self.selected and self.state.roi(key, self.selected) is None:
                self.selected = ""
                self.selection_changed.emit("")
            self._redraw()

    def _redraw(self) -> None:
        regions = [(r.roi_id, r.first_channel, r.last_channel) for r in self.rois()]
        self.plot.set_regions(regions, self.selected)
