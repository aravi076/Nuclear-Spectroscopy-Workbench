"""Peak Analysis page: automatic peak search. Calculations are delegated to ``nsw.services``."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from nsw.core.spectrum import Spectrum
from nsw.services import peaks as peaks_service
from nsw.ui import workers
from nsw.ui.plot import PlotItemSpec, SpectrumPlot
from nsw.ui.state import AppState, Entry
from nsw.ui.tables import DataTable

Runner = Callable[[workers.Worker, str], None]
CANDIDATE_NOTE = (
    "Candidates are not confirmed peaks: any sharp curvature of the continuum (for example a "
    "Compton edge) also gives a response, and lines closer than about one FWHM appear as one "
    "candidate. Width, area and height are estimates; fit the peaks for quantitative values."
)


def parse_numbers(text: str) -> tuple[float, ...]:
    """Numbers separated by commas, semicolons or blanks. Raises ValueError."""
    parts = text.replace(";", ",").replace(" ", ",").split(",")
    return tuple(float(p) for p in parts if p.strip())


def describe_entry(entry: Entry) -> str:
    s = entry.spectrum
    tag = "SYNTHETIC  " if s.is_synthetic else ""
    live = f"{s.live_time_s:g} s" if s.live_time_s else "no live time"
    return f"{tag}{entry.title}   [{live}, {s.measurement_status.value}]"


class PeakSearchPage(QWidget):
    def __init__(self, state: AppState, runner: Runner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._runner = runner
        self._entries: list[Entry] = []
        self._busy = False
        self.result: peaks_service.PeakSearchResult | None = None

        self.spectrum_box = QComboBox()
        self.spectrum_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.spectrum_box.setMinimumContentsLength(40)
        self.scales_edit = QLineEdit("4, 8")
        self.scales_edit.setToolTip(
            "Expected peak widths (FWHM, in channels) used as detection scales. A coarse "
            "ladder is enough: a scale detects peaks from about a third of its width up to "
            "its width."
        )
        self.estimate_button = QPushButton("Suggest from strongest peak")
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(2.0, 100.0)
        self.threshold_spin.setSingleStep(0.5)
        self.threshold_spin.setValue(4.0)
        self.threshold_spin.setToolTip("Minimum significance (filter response / its uncertainty).")
        self.separation_spin = QDoubleSpinBox()
        self.separation_spin.setRange(0.25, 5.0)
        self.separation_spin.setSingleStep(0.25)
        self.separation_spin.setValue(1.0)
        self.separation_spin.setToolTip("Minimum distance between candidates, in units of FWHM.")
        self.first_spin = QSpinBox()
        self.last_spin = QSpinBox()
        for spin in (self.first_spin, self.last_spin):
            spin.setRange(0, 65536)
        self.search_button = QPushButton("Search for peaks")

        scales_row = QHBoxLayout()
        scales_row.addWidget(self.scales_edit, 1)
        scales_row.addWidget(self.estimate_button)
        region_row = QHBoxLayout()
        region_row.addWidget(self.first_spin)
        region_row.addWidget(QLabel("to"))
        region_row.addWidget(self.last_spin)
        region_row.addStretch(1)
        form = QFormLayout()
        form.addRow("Spectrum:", self.spectrum_box)
        form.addRow("Detection scales (FWHM, channels):", scales_row)
        form.addRow("Significance threshold:", self.threshold_spin)
        form.addRow("Minimum separation (FWHM):", self.separation_spin)
        form.addRow("Channel region:", region_row)
        buttons = QHBoxLayout()
        buttons.addWidget(self.search_button)
        buttons.addStretch(1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMaximumHeight(90)
        self.plot = SpectrumPlot()
        self.plot.log_box.setChecked(True)
        self.table = DataTable()

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addLayout(form)
        top_layout.addLayout(buttons)
        top_layout.addWidget(self.status)
        top_layout.addWidget(self.messages)
        top_layout.addWidget(self.plot, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.table)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        QVBoxLayout(self).addWidget(split)

        self.search_button.clicked.connect(self.run)
        self.estimate_button.clicked.connect(self.suggest_scales)
        self.spectrum_box.currentIndexChanged.connect(self._spectrum_changed)
        self.table.itemSelectionChanged.connect(self._row_selected)
        state.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ population
    def refresh(self) -> None:
        self._entries = self.state.all_entries()
        current = self.spectrum_box.currentData()
        self.spectrum_box.blockSignals(True)
        self.spectrum_box.clear()
        for e in self._entries:
            self.spectrum_box.addItem(describe_entry(e), e.key)
        index = self.spectrum_box.findData(current)
        self.spectrum_box.setCurrentIndex(index if index >= 0 else (0 if self._entries else -1))
        self.spectrum_box.blockSignals(False)
        self._spectrum_changed()

    def select(self, key: str) -> None:
        self.spectrum_box.setCurrentIndex(self.spectrum_box.findData(key))

    def current_spectrum(self) -> Spectrum | None:
        i = self.spectrum_box.currentIndex()
        return self._entries[i].spectrum if 0 <= i < len(self._entries) else None

    def _set_status(self, text: str, colour: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-weight: bold;")

    def _update_enabled(self) -> None:
        ready = self.current_spectrum() is not None and not self._busy
        self.search_button.setEnabled(ready)
        self.estimate_button.setEnabled(ready)

    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self._update_enabled()
        if s is None:
            self.result = None
            self.plot.set_spectra([])
            self.plot.set_markers([])
            self.table.set_table([], [])
            self.messages.setPlainText("")
            self._set_status("Load a dataset to search for peaks.", "#555555")
            return
        self.first_spin.setValue(s.first_channel)
        self.last_spin.setValue(s.first_channel + s.n_channels - 1)
        self.plot.set_spectra([PlotItemSpec(s, "spectrum")])
        previous = self.state.peak_search(s.data_sha256)
        if previous is not None:
            self._show(previous)
        else:
            self.result = None
            self.plot.set_markers([])
            self.table.set_table([], [])
            self.messages.setPlainText("")
            self._set_status("No search has been run on this spectrum.", "#555555")

    # ------------------------------------------------------------------ actions
    def suggest_scales(self) -> None:
        s = self.current_spectrum()
        if s is None:
            return
        scales = peaks_service.default_scales(s)
        self.scales_edit.setText(", ".join(f"{w:g}" for w in scales))

    def settings(self) -> peaks_service.PeakSearchSettings:
        """Search settings from the controls. Raises ValueError / ProcessingError."""
        return peaks_service.PeakSearchSettings(
            fwhm_channels=parse_numbers(self.scales_edit.text()),
            threshold=self.threshold_spin.value(),
            min_separation_fwhm=self.separation_spin.value(),
            first_channel=self.first_spin.value(),
            last_channel=self.last_spin.value(),
        )

    def run(self) -> None:
        s = self.current_spectrum()
        if s is None or self._busy:
            return
        try:
            settings = self.settings()
        except (ValueError, peaks_service.ProcessingError) as exc:
            self._set_status(f"Search not started: {exc}", "#b00020")
            return
        self._busy = True
        self._update_enabled()
        self._set_status("Searching…", "#555555")
        worker = workers.Worker(peaks_service.search, s, settings)
        worker.signals.result.connect(self._done)
        worker.signals.error.connect(self._failed)
        self._runner(worker, "Searching for peaks…")

    def _failed(self, message: str, _traceback: str) -> None:
        self._busy = False
        self._update_enabled()
        self._set_status(f"Search refused: {message}", "#b00020")

    def _done(self, result: peaks_service.PeakSearchResult) -> None:
        self._busy = False
        self._update_enabled()
        self.state.set_peak_search(result)
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == result.spectrum_sha256:
            self._show(result)

    def _show(self, result: peaks_service.PeakSearchResult) -> None:
        s = self.current_spectrum()
        if s is None:
            return
        self.result = result
        headers, rows = peaks_service.candidate_table(result)
        self.table.set_table(headers, rows)
        self.plot.set_markers([c.channel for c in result.candidates])
        n = len(result.candidates)
        self._set_status(
            f"{n} candidate{'s' if n != 1 else ''} in channels {result.first_channel}–"
            f"{result.last_channel} at significance ≥ {result.settings.threshold:g}.   "
            f"{peaks_service.result_status(s)}",
            "#0b5cad" if s.is_synthetic else "#1b7f3b",
        )
        self.messages.setPlainText(
            "\n".join([CANDIDATE_NOTE, *(f"Caution: {w}" for w in result.warnings)])
        )

    def _row_selected(self) -> None:
        if self.result is None:
            return
        row = self.table.currentRow()
        if 0 <= row < len(self.result.candidates):
            c = self.result.candidates[row]
            half = 6.0 * c.fwhm_guess
            self.plot.zoom_to(c.channel - half, c.channel + half)
            self.plot.set_cursor_channel(round(c.channel))
