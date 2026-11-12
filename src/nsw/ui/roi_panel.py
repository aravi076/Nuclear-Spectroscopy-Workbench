"""Regions-of-interest panel: the regions of the shown spectrum with net counts and limits.

The detection-limit method is selectable (ISO 11929 framework or Currie); the default
comes from the settings file (``analysis.detection_limits``). Values are counts only,
so they are shown for experimental spectra too, labelled like every other result.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from nsw.services import candidates as cand
from nsw.services import roi as roi_service
from nsw.services.peaks import FitResult, result_status
from nsw.services.roi import ModelComparison
from nsw.ui.fit_plot import FitPlot
from nsw.ui.roi_controller import RoiController
from nsw.ui.tables import DataTable
from nsw.ui.workers import Worker

HELP = (
    "Press R (or the Region button) and drag along the spectrum to mark a region. Drag its "
    "edges to change it, Del deletes the selected region, Ctrl+Z / Ctrl+Y undo and redo. "
    "F (or Fit region) fits the selected region; right-click adds a peak at the cursor."
)


QUALITY_STYLES = {
    cand.CandidateQuality.STRONG: ("#2ca02c", Qt.PenStyle.SolidLine),
    cand.CandidateQuality.WEAK: ("#ff7f0e", Qt.PenStyle.SolidLine),
    cand.CandidateQuality.DOUBTFUL: ("#9467bd", Qt.PenStyle.DashLine),
    cand.CandidateQuality.NOT_PHOTOPEAK: ("#7f7f7f", Qt.PenStyle.DashLine),
}
EXCLUDED_STYLE = ("#c8c8c8", Qt.PenStyle.DotLine)
LEGEND = (
    "Markers: green strong, orange weak, purple dashed doubtful, grey dashed not "
    "photopeak-like, light grey dotted excluded."
)


class RoiPanel(QWidget):
    def __init__(self, controller: RoiController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.method_box = QComboBox()
        for value, text in roi_service.LIMIT_METHOD_LABELS.items():
            self.method_box.addItem(text, value)
        self.method_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.method_box.setMinimumContentsLength(16)
        self.method_box.setToolTip("Method for decision thresholds and detection limits")
        self.edge_box = QSpinBox()
        self.edge_box.setRange(1, 50)
        self.edge_box.setValue(roi_service.DEFAULT_EDGE_CHANNELS)
        self.edge_box.setToolTip("Channels at each end of a region used for the continuum")
        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        self.delete_button = QPushButton("Delete region")
        self.fit_button = QPushButton("Fit region (F)")
        self.compare_button = QPushButton("Compare models")
        self.tail_button = QPushButton("Test low-energy tail")
        self.tail_button.setToolTip(
            "Fit Gaussian + low-side tail and decide with a bootstrap threshold "
            "(takes several seconds; runs in the background)"
        )
        self.compare_button.setToolTip(
            "Fit with constant, linear and linear + step continuum; keep the simplest "
            "model unless a richer one improves the fit significantly (p < 0.01)"
        )
        self.fit_plot = FitPlot()
        self.comparison_table = DataTable()
        self.comparison_label = QLabel()
        self.comparison_label.setWordWrap(True)
        self.status = QLabel(HELP)
        self.status.setWordWrap(True)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.table = DataTable()
        self.auto_box = QCheckBox("Search for peaks on opening")
        self.auto_box.setToolTip("Search every spectrum for peak candidates when it is opened")
        self.auto_box.setChecked(True)
        self.threshold_box = QDoubleSpinBox()
        self.threshold_box.setRange(2.0, 50.0)
        self.threshold_box.setSingleStep(0.5)
        self.threshold_box.setValue(cand.DEFAULT_THRESHOLD)
        self.threshold_box.setSuffix(" σ")
        self.threshold_box.setToolTip("Sensitivity: minimum significance of a candidate")
        self.search_button = QPushButton("Search spectrum")
        self.search_region_button = QPushButton("Search region")
        self.exclude_button = QPushButton("Exclude selected")
        self.restore_button = QPushButton("Restore excluded")
        self.candidates_label = QLabel(LEGEND)
        self.candidates_label.setWordWrap(True)

        # rows kept short so the window fits 1366 × 768 (U8)
        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Limits:"))
        settings_row.addWidget(self.method_box, 1)
        self.edge_label = QLabel("Continuum channels:")
        settings_row.addWidget(self.edge_label)
        settings_row.addWidget(self.edge_box)
        controls = QHBoxLayout()
        for b in (
            self.fit_button,
            self.compare_button,
            self.tail_button,
            self.undo_button,
            self.redo_button,
            self.delete_button,
        ):
            controls.addWidget(b)
        controls.addStretch(1)
        search_options = QHBoxLayout()
        search_options.addWidget(self.auto_box)
        self.threshold_label = QLabel("Sensitivity:")
        search_options.addWidget(self.threshold_label)
        search_options.addWidget(self.threshold_box)
        search_options.addStretch(1)
        search_row = QHBoxLayout()
        for b in (
            self.search_button,
            self.search_region_button,
            self.exclude_button,
            self.restore_button,
        ):
            search_row.addWidget(b)
        search_row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(search_options)
        layout.addLayout(search_row)
        layout.addWidget(self.candidates_label)
        layout.addLayout(settings_row)
        layout.addLayout(controls)
        layout.addWidget(self.label)
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addWidget(self.comparison_label)
        lower_layout.addWidget(self.comparison_table)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.fit_plot)
        split.addWidget(lower)
        layout.addWidget(split, 1)
        layout.addWidget(self.status)

        self.method_box.currentIndexChanged.connect(self.refresh)
        self.edge_box.valueChanged.connect(self.refresh)
        self.undo_button.clicked.connect(controller.undo)
        self.redo_button.clicked.connect(controller.redo)
        self.delete_button.clicked.connect(controller.delete_selected)
        self.fit_button.clicked.connect(controller.fit_selected)
        self.search_button.clicked.connect(self.search_spectrum)
        self.search_region_button.clicked.connect(self.search_region)
        self.exclude_button.clicked.connect(controller.exclude_selected_markers)
        self.restore_button.clicked.connect(controller.restore_excluded)
        controller.state.peaks_changed.connect(self._peaks_changed)
        self.compare_button.clicked.connect(controller.compare_selected)
        self.tail_button.clicked.connect(self.start_tail_test)
        controller.fitted.connect(self.show_fit)
        controller.compared.connect(self.show_comparison)
        self.table.itemSelectionChanged.connect(self._table_selection)
        controller.message.connect(self.status.setText)
        controller.selection_changed.connect(self._show_selection)
        controller.state.rois_changed.connect(self._rois_changed)
        self.refresh()

    def set_method(self, method: str) -> None:
        index = self.method_box.findData(method)
        if index >= 0:
            self.method_box.setCurrentIndex(index)

    def method(self) -> str:
        return str(self.method_box.currentData())

    def refresh(self) -> None:
        spectrum = self.controller.spectrum
        stack = self.controller.stack
        self.undo_button.setEnabled(bool(stack and stack.canUndo()))
        self.redo_button.setEnabled(bool(stack and stack.canRedo()))
        for b in (self.delete_button, self.fit_button, self.compare_button):
            b.setEnabled(bool(self.controller.selected))
        if spectrum is None:
            self.label.setText("Select one spectrum to mark regions of interest.")
            self.table.set_table(list(roi_service.ROI_HEADERS), [])
            return
        self.label.setText(result_status(spectrum))
        settings = roi_service.limit_settings(self.method())
        rows = roi_service.roi_rows(
            spectrum, self.controller.rois(), settings, self.edge_box.value()
        )
        self.table.blockSignals(True)
        self.table.set_table(list(roi_service.ROI_HEADERS), rows)
        self.table.blockSignals(False)
        self._show_selection(self.controller.selected)

    # ------------------------------------------------------------------ candidates
    def search_spectrum(self) -> None:
        self.controller.search(self.threshold_box.value())

    def search_region(self) -> None:
        self.controller.search(self.threshold_box.value(), in_selected_region=True)

    def spectrum_opened(self) -> None:
        """Called when the shown spectrum changes: search once if asked to."""
        spectrum = self.controller.spectrum
        if spectrum is None:
            self.paint_candidates()
            return
        if (
            self.auto_box.isChecked()
            and self.controller.state.peak_search(spectrum.data_sha256) is None
        ):
            self.controller.search(self.threshold_box.value())
        self.paint_candidates()

    def paint_candidates(self) -> None:
        classified = self.controller.classified()
        channels = [cc.candidate.channel for cc in classified]
        styles = [
            EXCLUDED_STYLE if cc.excluded else QUALITY_STYLES[cc.quality] for cc in classified
        ]
        self.controller.plot.set_markers(channels, styles)
        text = cand.summary(classified) if classified else "No peak search yet."
        self.candidates_label.setText(text + "  " + LEGEND)

    def _peaks_changed(self, key: str) -> None:
        spectrum = self.controller.spectrum
        if spectrum is not None and key == spectrum.data_sha256:
            self.paint_candidates()

    def show_fit(self, result: FitResult) -> None:
        spectrum = self.controller.spectrum
        if spectrum is not None:
            self.fit_plot.set_result(result, result_status(spectrum))
            settings = roi_service.limit_settings(self.method())
            self.comparison_label.setText(roi_service.fit_limit_text(result, settings))

    def start_tail_test(self) -> None:
        spectrum, roi = self.controller.spectrum, self.controller.selected_roi()
        if spectrum is None or roi is None or not roi.peak_channels:
            self.status.setText("Select a fitted region (with peaks) first.")
            return
        self.tail_button.setEnabled(False)
        self.status.setText("Testing the low-energy tail (bootstrap, in the background)…")
        self._tail_worker = Worker(roi_service.run_tail_test, spectrum, roi)
        self._tail_worker.signals.result.connect(self._tail_done)
        self._tail_worker.signals.error.connect(self._tail_failed)
        QThreadPool.globalInstance().start(self._tail_worker)

    def _tail_done(self, test: object) -> None:
        self.tail_button.setEnabled(True)
        self.comparison_label.setText(roi_service.tail_text(test))  # type: ignore[arg-type]
        self.status.setText("Tail test finished.")

    def _tail_failed(self, message: str, _trace: str) -> None:
        self.tail_button.setEnabled(True)
        self.status.setText(f"Tail test failed: {message}")

    def show_comparison(self, comparison: ModelComparison) -> None:
        previous = self.comparison_label.text()
        limits = previous if previous.startswith("Limits from the fit") else ""
        self.comparison_label.setText(
            "Model comparison: " + comparison.reason + (chr(10) + limits if limits else "")
        )
        self.comparison_table.set_table(
            list(roi_service.COMPARISON_HEADERS), roi_service.comparison_rows(comparison)
        )

    def _rois_changed(self, key: str) -> None:
        spectrum = self.controller.spectrum
        if spectrum is not None and key == spectrum.data_sha256:
            self.refresh()

    def _show_selection(self, roi_id: str) -> None:
        for b in (self.delete_button, self.fit_button, self.compare_button):
            b.setEnabled(bool(roi_id))
        ids = [r.roi_id for r in self.controller.rois()]
        self.table.blockSignals(True)
        self.table.clearSelection()
        if roi_id in ids:
            self.table.selectRow(ids.index(roi_id))
        self.table.blockSignals(False)

    def _table_selection(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        ids = [r.roi_id for r in self.controller.rois()]
        if len(rows) == 1:
            (row,) = rows
            if row < len(ids):
                self.controller.select(ids[row])
                roi = self.controller.selected_roi()
                if roi is not None:
                    margin = max(5, roi.n_channels // 2)
                    self.controller.plot.zoom_to(
                        roi.first_channel - margin, roi.last_channel + margin
                    )
