"""Peak Fitting page: Gaussian fits of chosen regions. Calculations are in ``nsw.services``."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nsw.core.spectrum import Spectrum
from nsw.services import peaks as peaks_service
from nsw.ui import workers
from nsw.ui.explorer import KeyValueTable
from nsw.ui.fit_plot import FitPlot
from nsw.ui.peak_search_page import Runner, describe_entry, parse_numbers
from nsw.ui.state import AppState, Entry
from nsw.ui.tables import DataTable

METHODS = (
    ("Automatic (Poisson for counts, least squares for net data)", "auto"),
    ("Poisson likelihood", "poisson"),
    ("Weighted least squares", "wls"),
    ("Joint fit with a designated background", "joint"),
)
CONTINUA = (
    ("Linear", peaks_service.BackgroundShape.LINEAR),
    ("Constant", peaks_service.BackgroundShape.CONSTANT),
    ("None", peaks_service.BackgroundShape.NONE),
)
MANUAL = "Manual entry"


class PeakFitPage(QWidget):
    export_requested = Signal()

    def __init__(self, state: AppState, runner: Runner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._runner = runner
        self._entries: list[Entry] = []
        self._backgrounds: list[Entry] = []
        self._busy = False
        self._pending_key = ""  # spectrum of the running fit (results arrive by signal)
        self.last_result: peaks_service.FitResult | None = None
        self.last_evidence: peaks_service.MultipletEvidence | None = None

        self.spectrum_box = QComboBox()
        self.group_box = QComboBox()
        self.background_box = QComboBox()
        for box in (self.spectrum_box, self.group_box, self.background_box):
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            box.setMinimumContentsLength(40)
        self.first_spin = QSpinBox()
        self.last_spin = QSpinBox()
        for spin in (self.first_spin, self.last_spin):
            spin.setRange(0, 65536)
        self.centroids_edit = QLineEdit()
        self.centroids_edit.setPlaceholderText("peak positions in channels, e.g. 512.3, 530")
        self.fwhm_edit = QLineEdit("4")
        self.fwhm_edit.setToolTip("Starting width (FWHM, channels): one value, or one per peak.")
        self.method_box = QComboBox()
        for label, value in METHODS:
            self.method_box.addItem(label, value)
        self.continuum_box = QComboBox()
        for label, shape in CONTINUA:
            self.continuum_box.addItem(label, shape)
        self.step_check = QCheckBox("Step under each peak")
        self.step_check.setToolTip(
            "Adds a smoothed step on the low side, proportional to the peak height. Use only "
            "where physically justified (HPGe peaks with many counts)."
        )
        self.shared_check = QCheckBox("Common width for all peaks of the region")
        self.shared_check.setChecked(True)
        self.fix_position_check = QCheckBox("Hold positions fixed")
        self.fix_width_check = QCheckBox("Hold width fixed at the FWHM guess")
        for check in (self.fix_position_check, self.fix_width_check):
            check.setToolTip(
                "For weak peaks whose position or width is known from a calibration: a free "
                "shape biases the area of marginal peaks upwards. The uncertainty of the "
                "fixed value is not propagated."
            )
        self.fit_button = QPushButton("Fit region")
        self.fit_all_button = QPushButton("Fit all candidate groups")
        self.evidence_button = QPushButton("Assess multiplet")
        self.export_button = QPushButton("Export fitted peaks as CSV…")
        self.clear_button = QPushButton("Discard results")

        region_row = QHBoxLayout()
        region_row.addWidget(self.first_spin)
        region_row.addWidget(QLabel("to"))
        region_row.addWidget(self.last_spin)
        region_row.addStretch(1)
        options_row = QHBoxLayout()
        options_row.addWidget(self.continuum_box)
        options_row.addWidget(self.step_check)
        options_row.addWidget(self.shared_check)
        options_row.addStretch(1)
        fixed_row = QHBoxLayout()
        fixed_row.addWidget(self.fix_position_check)
        fixed_row.addWidget(self.fix_width_check)
        fixed_row.addStretch(1)
        form = QFormLayout()
        form.addRow("Spectrum:", self.spectrum_box)
        form.addRow("Candidate group:", self.group_box)
        form.addRow("Fit region (channels):", region_row)
        form.addRow("Peak positions:", self.centroids_edit)
        form.addRow("FWHM guess (channels):", self.fwhm_edit)
        form.addRow("Estimator:", self.method_box)
        form.addRow("Background (joint fit):", self.background_box)
        form.addRow("Continuum:", options_row)
        form.addRow("Known shape:", fixed_row)
        buttons = QHBoxLayout()
        for b in (
            self.fit_button,
            self.fit_all_button,
            self.evidence_button,
            self.export_button,
            self.clear_button,
        ):
            buttons.addWidget(b)
        buttons.addStretch(1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.plot = FitPlot()
        self.peak_table = DataTable()
        self.summary_table = KeyValueTable()
        tabs = QTabWidget()
        tabs.addTab(self.peak_table, "Fitted peaks")
        tabs.addTab(self.summary_table, "Details of the displayed fit")

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addLayout(form)
        top_layout.addLayout(buttons)
        top_layout.addWidget(self.status)
        top_layout.addWidget(self.plot, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        QVBoxLayout(self).addWidget(split)

        self.fit_button.clicked.connect(self.run)
        self.fit_all_button.clicked.connect(self.run_all)
        self.evidence_button.clicked.connect(self.run_evidence)
        self.export_button.clicked.connect(self.export_requested)
        self.clear_button.clicked.connect(self.discard_results)
        self.spectrum_box.currentIndexChanged.connect(self._spectrum_changed)
        self.group_box.currentIndexChanged.connect(self._group_changed)
        self.method_box.currentIndexChanged.connect(self._update_enabled)
        self.centroids_edit.textChanged.connect(self._update_enabled)
        self.peak_table.itemSelectionChanged.connect(self._row_selected)
        state.changed.connect(self.refresh)
        state.peaks_changed.connect(self._search_changed)
        self.refresh()

    # ------------------------------------------------------------------ population
    def refresh(self) -> None:
        self._entries = self.state.all_entries()
        self._backgrounds = self.state.background_entries()
        for box, entries in (
            (self.spectrum_box, self._entries),
            (self.background_box, self._backgrounds),
        ):
            current = box.currentData()
            box.blockSignals(True)
            box.clear()
            for e in entries:
                box.addItem(describe_entry(e), e.key)
            index = box.findData(current)
            box.setCurrentIndex(index if index >= 0 else (0 if entries else -1))
            box.blockSignals(False)
        self._spectrum_changed()

    def select(self, key: str) -> None:
        self.spectrum_box.setCurrentIndex(self.spectrum_box.findData(key))

    def current_spectrum(self) -> Spectrum | None:
        i = self.spectrum_box.currentIndex()
        return self._entries[i].spectrum if 0 <= i < len(self._entries) else None

    def current_background(self) -> Spectrum | None:
        i = self.background_box.currentIndex()
        return self._backgrounds[i].spectrum if 0 <= i < len(self._backgrounds) else None

    def results(self) -> list[peaks_service.FitResult]:
        s = self.current_spectrum()
        return self.state.fits(s.data_sha256) if s is not None else []

    def _set_status(self, text: str, colour: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-weight: bold;")

    def _centroid_count(self) -> int:
        try:
            return len(parse_numbers(self.centroids_edit.text()))
        except ValueError:
            return 0

    def _update_enabled(self) -> None:
        s = self.current_spectrum()
        ready = s is not None and not self._busy
        joint = self.method_box.currentData() == "joint"
        n = self._centroid_count()
        self.background_box.setEnabled(joint)
        self.fit_button.setEnabled(ready and n >= 1 and (not joint or bool(self._backgrounds)))
        has_search = s is not None and self.state.peak_search(s.data_sha256) is not None
        self.fit_all_button.setEnabled(ready and has_search and not joint)
        self.evidence_button.setEnabled(ready and n >= 2 and not joint)
        self.export_button.setEnabled(bool(self.results()))
        self.clear_button.setEnabled(bool(self.results()))

    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self.last_result = None
        self.last_evidence = None
        self.plot.clear()
        self.summary_table.set_rows([])
        self._fill_groups()
        self._show_table()
        if s is None:
            self._set_status("Load a dataset to fit peaks.", "#555555")
        else:
            self._set_status(
                "Choose a candidate group or enter a region and peak positions.", "#555555"
            )
        self._update_enabled()

    def _search_changed(self, key: str) -> None:
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._fill_groups()
            self._update_enabled()

    def _fill_groups(self) -> None:
        s = self.current_spectrum()
        self.group_box.blockSignals(True)
        self.group_box.clear()
        self.group_box.addItem(MANUAL, -1)
        found = self.state.peak_search(s.data_sha256) if s is not None else None
        if found is not None:
            for group in found.groups():
                where = ", ".join(f"{c.channel:.1f}" for c in group)
                self.group_box.addItem(
                    f"Group {group[0].group + 1}: {len(group)} candidate"
                    f"{'s' if len(group) != 1 else ''} at channel {where}",
                    group[0].group,
                )
        self.group_box.setCurrentIndex(0)
        self.group_box.blockSignals(False)

    def _group_changed(self) -> None:
        s = self.current_spectrum()
        number = self.group_box.currentData()
        if s is None or number is None or number < 0:
            return
        found = self.state.peak_search(s.data_sha256)
        if found is None:
            return
        group = next(g for g in found.groups() if g[0].group == number)
        first, last = peaks_service.group_region(s, group)
        self.first_spin.setValue(first)
        self.last_spin.setValue(last)
        self.centroids_edit.setText(", ".join(f"{c.channel:.2f}" for c in group))
        self.fwhm_edit.setText(", ".join(f"{c.fwhm_guess:.2f}" for c in group))

    def select_group(self, number: int) -> None:
        """Select the candidate group with 0-based group number ``number``."""
        self.group_box.setCurrentIndex(self.group_box.findData(number))

    # ------------------------------------------------------------------ inputs
    def fit_settings(self) -> peaks_service.FitSettings:
        method = self.method_box.currentData()
        return peaks_service.FitSettings(
            method=peaks_service.FitMethod.AUTO
            if method == "joint"
            else peaks_service.FitMethod(method),
            background=self.continuum_box.currentData(),
            shared_width=self.shared_check.isChecked(),
            step=self.step_check.isChecked(),
            fixed_centroids=self.fix_position_check.isChecked(),
            fixed_width=self.fix_width_check.isChecked(),
        )

    def _inputs(self) -> tuple[int, int, tuple[float, ...], float | tuple[float, ...]]:
        centroids = parse_numbers(self.centroids_edit.text())
        widths = parse_numbers(self.fwhm_edit.text())
        if not widths:
            raise ValueError("a FWHM guess is required")
        fwhm: float | tuple[float, ...] = widths[0] if len(widths) == 1 else widths
        return self.first_spin.value(), self.last_spin.value(), centroids, fwhm

    def _start(self, worker: workers.Worker, message: str) -> None:
        self._busy = True
        self._update_enabled()
        self._set_status(message, "#555555")
        worker.signals.error.connect(self._failed)
        self._runner(worker, message)

    def _failed(self, message: str, _traceback: str) -> None:
        self._busy = False
        self._update_enabled()
        self._set_status(f"Refused: {message}", "#b00020")

    # ------------------------------------------------------------------ actions
    def run(self) -> None:
        s = self.current_spectrum()
        if s is None or self._busy:
            return
        try:
            first, last, centroids, fwhm = self._inputs()
            settings = self.fit_settings()
        except (ValueError, peaks_service.ProcessingError) as exc:
            self._set_status(f"Fit not started: {exc}", "#b00020")
            return
        if self.method_box.currentData() == "joint":
            background = self.current_background()
            if background is None:
                self._set_status("Designate a background measurement first.", "#b00020")
                return
            worker = workers.Worker(
                peaks_service.fit_joint, s, background, first, last, centroids, fwhm, settings
            )
        else:
            worker = workers.Worker(peaks_service.fit, s, first, last, centroids, fwhm, settings)
        self._pending_key = s.data_sha256
        worker.signals.result.connect(self._fit_done)
        self._start(worker, "Fitting…")

    def run_all(self) -> None:
        s = self.current_spectrum()
        found = self.state.peak_search(s.data_sha256) if s is not None else None
        if s is None or found is None or self._busy:
            return
        try:
            settings = self.fit_settings()
        except peaks_service.ProcessingError as exc:
            self._set_status(f"Fit not started: {exc}", "#b00020")
            return
        worker = workers.Worker(peaks_service.fit_all, s, found, settings, with_progress=True)
        self._pending_key = s.data_sha256
        worker.signals.result.connect(self._all_done)
        self._start(worker, "Fitting all candidate groups…")

    def run_evidence(self) -> None:
        s = self.current_spectrum()
        if s is None or self._busy:
            return
        try:
            first, last, centroids, fwhm = self._inputs()
            settings = self.fit_settings()
        except (ValueError, peaks_service.ProcessingError) as exc:
            self._set_status(f"Assessment not started: {exc}", "#b00020")
            return
        worker = workers.Worker(peaks_service.evidence, s, first, last, centroids, fwhm, settings)
        worker.signals.result.connect(self._evidence_done)
        self._start(worker, "Comparing multiplet models…")

    def _store(self, key: str, result: peaks_service.FitResult) -> None:
        self.state.store_fit(key, result)

    def _fit_done(self, result: peaks_service.FitResult) -> None:
        key = self._pending_key
        self._busy = False
        self._store(key, result)
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._show_table()
            self._show_fit(result)
        self._update_enabled()

    def _all_done(self, fits: tuple[peaks_service.CandidateFit, ...]) -> None:
        key = self._pending_key
        self._busy = False
        done = [f.result for f in fits if f.result is not None]
        for r in done:
            self._store(key, r)
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._show_table()
            if done:
                self._show_fit(done[0])
            refused = len(fits) - len(done)
            good = sum(1 for r in done if r.ok and not r.poor_fit)
            self._set_status(
                f"{len(done)} groups fitted: {good} acceptable, {len(done) - good} flagged or "
                f"poor, {refused} could not be fitted. Select a row to inspect a fit.",
                "#1b7f3b" if good == len(fits) else "#b26a00",
            )
        self._update_enabled()

    def _evidence_done(self, ev: peaks_service.MultipletEvidence) -> None:
        self._busy = False
        self.last_evidence = ev
        self.summary_table.set_rows(peaks_service.evidence_rows(ev))
        self._set_status(ev.statement, "#1b7f3b" if ev.supported else "#b26a00")
        self._update_enabled()

    def _show_table(self) -> None:
        headers, rows = peaks_service.peak_table(self.results())
        self.peak_table.blockSignals(True)
        self.peak_table.set_table(headers, rows)
        self.peak_table.blockSignals(False)

    def _show_fit(self, result: peaks_service.FitResult) -> None:
        s = self.current_spectrum()
        if s is None:
            return
        self.last_result = result
        self.plot.set_result(result, peaks_service.result_status(s))
        self.summary_table.set_rows(peaks_service.fit_summary_rows(s, result))
        if not result.converged or result.flags:
            self._set_status(
                "Fit flagged — do not use these values: " + "; ".join(result.flags or ("",)),
                "#b00020",
            )
        elif result.poor_fit:
            self._set_status(
                "The model does not describe the data (see goodness of fit); the values are "
                "not reliable.",
                "#b00020",
            )
        else:
            self._set_status("Fit converged. Check the residuals and the cautions.", "#1b7f3b")

    def _row_selected(self) -> None:
        row = self.peak_table.currentRow()
        if row < 0:
            return
        n = 0
        for r in self.results():
            if row < n + r.n_peaks:
                self._show_fit(r)
                return
            n += r.n_peaks

    def discard_results(self) -> None:
        s = self.current_spectrum()
        if s is not None:
            self.state.clear_fits(s.data_sha256)
        self._spectrum_changed()

    def suggested_filename(self) -> str:
        s = self.current_spectrum()
        if s is None:
            return "peaks.csv"
        stem = Path(s.source.filename).stem if s.source else (s.synthetic_id or "spectrum")
        return f"{stem}_peaks.csv"
