"""Nuclear Data Explorer page: libraries, records with provenance, line overlay, matching.

All logic is in ``nsw.services.nuclear_explorer``. The page shows only libraries loaded
in this session (the fictitious test library and imported reference-data files); NSW
contains no real nuclear data. Reference lines are drawn only on spectra with a VERIFIED
energy calibration (and, for measured spectra, only when experimental verification is
enabled); otherwise the reason is shown and the overlay stays off. Matching ranks lines
for one fitted peak — it does not identify a nuclide. Accept / reject decisions are
recorded in the session (``AppState.assignment_decisions``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nsw.core.nuclear_data import ReferenceLibrary
from nsw.core.spectrum import Spectrum
from nsw.services import identification as ids
from nsw.services import nuclear_explorer as nx
from nsw.ui.explorer import KeyValueTable
from nsw.ui.peak_search_page import describe_entry
from nsw.ui.plot import PlotItemSpec, PlotMode, SpectrumPlot
from nsw.ui.state import AppState, Entry
from nsw.ui.tables import DataTable


class NuclearDataPage(QWidget):
    load_library_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._entries: list[Entry] = []
        self._matches: list[nx.LineMatch] = []
        self._peak_label = ""

        self.library_box = QComboBox()
        self.load_button = QPushButton("Load reference-data file…")
        self.spectrum_box = QComboBox()
        self.spectrum_box.setMinimumContentsLength(24)
        self.spectrum_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.library_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.library_box.setMinimumContentsLength(20)
        self.nuclide_table = DataTable()
        self.nuclide_table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.line_table = DataTable()
        self.provenance_table = KeyValueTable()
        self.conflict_table = DataTable()
        self.details = QTabWidget()
        self.details.addTab(self.line_table, "Lines")
        self.details.addTab(self.provenance_table, "Provenance")
        self.details.addTab(self.conflict_table, "Evaluation differences")
        self.min_fraction_box = QDoubleSpinBox()
        self.min_fraction_box.setRange(0.0, 1.0)
        self.min_fraction_box.setSingleStep(0.05)
        self.min_fraction_box.setValue(0.05)
        self.min_fraction_box.setToolTip("Show lines at least this fraction of the strongest")
        self.overlay_button = QPushButton("Show lines of the selected nuclides")
        self.clear_button = QPushButton("Clear lines")
        self.gate = QLabel()
        self.gate.setWordWrap(True)
        self.plot = SpectrumPlot()
        self.match_button = QPushButton("Match selected peak")
        self.match_button.setToolTip(
            "Select a fitted peak marker (S, then click) and list compatible reference lines"
        )
        self.accept_button = QPushButton("Accept assignment")
        self.reject_button = QPushButton("Reject assignment")
        self.match_label = QLabel(
            "Fitted peaks are marked; select one (S, then click) and press Match."
        )
        self.match_label.setWordWrap(True)
        self.match_table = DataTable()

        top = QHBoxLayout()
        top.addWidget(QLabel("Library:"))
        top.addWidget(self.library_box, 1)
        top.addWidget(self.load_button)
        top.addWidget(QLabel("Spectrum:"))
        top.addWidget(self.spectrum_box, 1)
        browse = QSplitter(Qt.Orientation.Horizontal)
        browse.addWidget(self.nuclide_table)
        browse.addWidget(self.details)
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(QLabel("Minimum relative intensity:"))
        overlay_row.addWidget(self.min_fraction_box)
        overlay_row.addWidget(self.overlay_button)
        overlay_row.addWidget(self.clear_button)
        overlay_row.addStretch(1)
        match_row = QHBoxLayout()
        for b in (self.match_button, self.accept_button, self.reject_button):
            match_row.addWidget(b)
        match_row.addStretch(1)
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addLayout(overlay_row)
        lower_layout.addLayout(match_row)
        lower_layout.addWidget(self.gate)
        lower_layout.addWidget(self.plot, 3)
        lower_layout.addWidget(self.match_label)
        lower_layout.addWidget(self.match_table, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(browse)
        split.addWidget(lower)
        split.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)

        self.load_button.clicked.connect(self.load_library_requested)
        self.library_box.currentIndexChanged.connect(self._library_changed)
        self.spectrum_box.currentIndexChanged.connect(self._spectrum_changed)
        self.nuclide_table.itemSelectionChanged.connect(self._nuclide_selected)
        self.overlay_button.clicked.connect(self.show_overlay)
        self.clear_button.clicked.connect(self.clear_overlay)
        self.match_button.clicked.connect(self.match_selected_peak)
        self.accept_button.clicked.connect(self.accept_assignment)
        self.reject_button.clicked.connect(self.reject_assignment)
        state.changed.connect(self.refresh)
        state.fits_changed.connect(self._fits_changed)
        state.libraries_changed.connect(self._refresh_libraries)
        self._refresh_libraries()
        self.refresh()

    # ------------------------------------------------------------------ population
    def _refresh_libraries(self) -> None:
        """Rebuild the library list; a newly loaded library becomes the current one."""
        previous_count = self.library_box.count()
        current = self.library_box.currentIndex()
        self.library_box.blockSignals(True)
        self.library_box.clear()
        for library in self.state.libraries:
            self.library_box.addItem(nx.library_label(library))
        count = self.library_box.count()
        chosen = count - 1 if count > previous_count else current
        self.library_box.setCurrentIndex(max(0, min(chosen, count - 1)))
        self.library_box.blockSignals(False)
        self._library_changed()

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

    def current_library(self) -> ReferenceLibrary:
        return self.state.libraries[max(0, self.library_box.currentIndex())]

    def current_spectrum(self) -> Spectrum | None:
        i = self.spectrum_box.currentIndex()
        return self._entries[i].spectrum if 0 <= i < len(self._entries) else None

    def select_spectrum(self, key: str) -> None:
        self.spectrum_box.setCurrentIndex(self.spectrum_box.findData(key))

    def selected_nuclides(self) -> list[str]:
        rows = sorted({i.row() for i in self.nuclide_table.selectedIndexes()})
        records = self.current_library().records
        return [records[r].nuclide for r in rows if r < len(records)]

    def select_nuclides(self, names: list[str]) -> None:
        self.nuclide_table.clearSelection()
        mode = self.nuclide_table.selectionMode()
        self.nuclide_table.setSelectionMode(DataTable.SelectionMode.MultiSelection)
        for r, record in enumerate(self.current_library().records):
            if record.nuclide in names:
                self.nuclide_table.selectRow(r)
        self.nuclide_table.setSelectionMode(mode)

    # ------------------------------------------------------------------ browsing
    def _library_changed(self) -> None:
        if not self.state.libraries:
            return
        library = self.current_library()
        self.nuclide_table.set_table(*nx.nuclide_table(library))
        self.conflict_table.set_table(*nx.conflict_table(library))
        self.line_table.set_table([], [])
        self.provenance_table.set_rows([])
        self.clear_overlay()
        self._update_gate()

    def _nuclide_selected(self) -> None:
        rows = sorted({i.row() for i in self.nuclide_table.selectedIndexes()})
        records = self.current_library().records
        if not rows or rows[0] >= len(records):
            return
        record = records[rows[0]]
        self.line_table.set_table(*nx.line_table(record))
        self.provenance_table.set_rows(nx.provenance_rows(record))

    # ------------------------------------------------------------------ spectrum and overlay
    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self._matches = []
        self.match_table.set_table(list(nx.MATCH_HEADERS), [])
        if s is None:
            self.plot.set_spectra([])
        else:
            title = self._entries[self.spectrum_box.currentIndex()].title
            self.plot.set_spectra([PlotItemSpec(s, title)])
        self._mark_fitted_peaks()
        self.clear_overlay()
        self._update_gate()

    def _fits_changed(self, key: str) -> None:
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._mark_fitted_peaks()

    def _mark_fitted_peaks(self) -> None:
        s = self.current_spectrum()
        channels = []
        if s is not None:
            channels = [p.centroid for f in self.state.fits(s.data_sha256) for p in f.peaks]
        self.plot.set_markers(channels)

    def _update_gate(self) -> None:
        s = self.current_spectrum()
        if s is None or not self.state.libraries:
            self.gate.setText("Load a dataset to show reference lines on a spectrum.")
            enabled = False
        else:
            missing = nx.overlay_missing(s, self.current_library())
            enabled = not missing
            self.gate.setText(
                "Reference lines can be drawn on this spectrum."
                if enabled
                else "Reference lines are not drawn: missing " + "; ".join(missing)
            )
        self.gate.setStyleSheet(f"color: {'#0b5cad' if enabled else '#b00020'};")
        for b in (self.overlay_button, self.match_button):
            b.setEnabled(enabled)
        self.accept_button.setEnabled(enabled and bool(self._matches))
        self.reject_button.setEnabled(enabled and bool(self._matches))

    def show_overlay(self) -> None:
        s = self.current_spectrum()
        nuclides = self.selected_nuclides()
        if s is None or not nuclides:
            self.gate.setText("Select one or more nuclides in the table first.")
            return
        try:
            lines = nx.overlay_lines(
                s, self.current_library(), nuclides, self.min_fraction_box.value()
            )
        except ids.ProcessingError as exc:
            self.gate.setText(str(exc))
            return
        self.plot.set_reference_lines(
            [(ln.channel, ln.label, ln.relative_strength) for ln in lines]
        )
        self.gate.setText(
            f"{len(lines)} reference line(s) of {', '.join(nuclides)} drawn "
            "(positions from the spectrum's verified calibration)."
        )

    def clear_overlay(self) -> None:
        self.plot.set_reference_lines([])

    # ------------------------------------------------------------------ matching
    def match_selected_peak(self) -> None:
        s = self.current_spectrum()
        chosen = self.plot.selected_markers()
        if s is None or not chosen:
            self.plot.set_mode(PlotMode.SELECT)
            self.match_label.setText("Select a fitted peak marker first (S, then click).")
            return
        try:
            peak, self._matches = nx.match_peak(
                s, self.state.fits(s.data_sha256), chosen[0], self.current_library()
            )
        except ids.ProcessingError as exc:
            self.match_label.setText(str(exc))
            return
        self._peak_label = f"{peak.energy_kev:.3f} keV ({peak.label})"
        self.match_table.set_table(list(nx.MATCH_HEADERS), nx.match_rows(self._matches))
        self.match_label.setText(
            f"Peak at {self._peak_label}: {len(self._matches)} compatible line(s), ranked by "
            "other strong lines found, then by |ΔE|/σ. Candidates, not an identification."
        )
        self._update_gate()

    def _chosen_match(self) -> nx.LineMatch | None:
        rows = sorted({i.row() for i in self.match_table.selectedIndexes()})
        if not rows or rows[0] >= len(self._matches):
            self.match_label.setText("Select a row of the match table first.")
            return None
        return self._matches[rows[0]]

    def _decide(self, accepted: bool) -> None:
        s = self.current_spectrum()
        match = self._chosen_match()
        if s is None or match is None:
            return
        self.state.record_assignment(
            s.data_sha256,
            {
                "peak": self._peak_label,
                "nuclide": match.nuclide,
                "evaluation": match.evaluation,
                "line_kev": match.line.energy_kev,
                "delta_kev": match.delta_kev,
                "sigma_kev": match.sigma_kev,
                "accepted": accepted,
            },
        )
        verb = "accepted" if accepted else "rejected"
        self.match_label.setText(
            f"Assignment {match.nuclide} {match.line.energy_kev:.3f} keV to the peak at "
            f"{self._peak_label} {verb} (recorded for this session)."
        )

    def accept_assignment(self) -> None:
        self._decide(True)

    def reject_assignment(self) -> None:
        self._decide(False)
