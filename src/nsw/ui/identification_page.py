"""Nuclide Identification page: ranked nuclide candidates with their evidence.

The page never reports an identification: it lists candidates with a confidence class
and the evidence for and against each. It is refused for spectra without a verified
energy calibration and for every experimental spectrum while experimental verification
is disabled; the reason is shown. NSW contains no nuclear data: synthetic spectra are
compared with the fictitious test library, and a reference-data file with provenance
can be loaded (``docs/nuclear_data.md``). Logic is in ``nsw.services.identification``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from nsw.core.spectrum import Spectrum
from nsw.services import identification as ids
from nsw.ui.explorer import KeyValueTable
from nsw.ui.peak_search_page import describe_entry
from nsw.ui.state import AppState, Entry
from nsw.ui.tables import DataTable

FICTITIOUS = "fictitious"


class IdentificationPage(QWidget):
    load_library_requested = Signal()
    export_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._entries: list[Entry] = []
        self.libraries: dict[str, ids.ReferenceLibrary] = {FICTITIOUS: ids.fictitious_library()}
        self.result: ids.IdentificationResult | None = None

        self.spectrum_box = QComboBox()
        self.spectrum_box.setMinimumContentsLength(40)
        self.spectrum_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.library_box = QComboBox()
        self.library_box.addItem("Fictitious test library (synthetic spectra only)", FICTITIOUS)
        self.load_button = QPushButton("Load reference-data file…")
        self.k_box = QDoubleSpinBox()
        self.k_box.setRange(1.0, 10.0)
        self.k_box.setSingleStep(0.5)
        self.k_box.setValue(3.0)
        self.k_box.setToolTip(
            "Coverage factor of the energy window |ΔE| ≤ k·σ. With k = 3 about one true line "
            "in 370 is lost; a larger k admits more chance coincidences."
        )
        self.extra_box = QDoubleSpinBox()
        self.extra_box.setRange(0.0, 50.0)
        self.extra_box.setDecimals(3)
        self.extra_box.setSuffix(" keV")
        self.extra_box.setToolTip("Additional energy uncertainty, e.g. for gain drift")
        self.efficiency_check = QCheckBox("Use the efficiency curve of this session")
        self.run_button = QPushButton("Rank nuclide candidates")
        self.export_button = QPushButton("Export candidates as CSV…")
        library_row = QHBoxLayout()
        library_row.addWidget(self.library_box, 1)
        library_row.addWidget(self.load_button)
        form = QFormLayout()
        form.addRow("Spectrum:", self.spectrum_box)
        form.addRow("Reference library:", library_row)
        form.addRow("Window factor k:", self.k_box)
        form.addRow("Extra energy uncertainty:", self.extra_box)
        form.addRow("", self.efficiency_check)
        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)
        self.gate = QLabel()
        self.gate.setWordWrap(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        notice = QLabel(f"<i>{ids.LIMITATION}</i>")
        notice.setWordWrap(True)
        self.candidate_table = DataTable()
        self.evidence_table = KeyValueTable()

        top = QVBoxLayout()
        top.addLayout(form)
        top.addWidget(self.gate)
        top.addLayout(buttons)
        top.addWidget(self.status)
        top.addWidget(notice)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.candidate_table)
        split.addWidget(self.evidence_table)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)

        self.spectrum_box.currentIndexChanged.connect(self._spectrum_changed)
        self.library_box.currentIndexChanged.connect(self._spectrum_changed)
        self.run_button.clicked.connect(self.run)
        self.load_button.clicked.connect(self.load_library_requested)
        self.export_button.clicked.connect(self.export_requested)
        self.candidate_table.itemSelectionChanged.connect(self._candidate_selected)
        state.changed.connect(self.refresh)
        state.fits_changed.connect(self._fits_changed)
        state.efficiency_changed.connect(self._spectrum_changed)
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

    def current_library(self) -> ids.ReferenceLibrary:
        return self.libraries[str(self.library_box.currentData())]

    def add_library(self, library: ids.ReferenceLibrary) -> None:
        key = f"file:{library.name}:{len(self.libraries)}"
        self.libraries[key] = library
        label = (
            f"{library.name} ({', '.join(library.evaluations)}; {len(library.nuclides)} nuclides)"
        )
        self.library_box.addItem(label, key)
        self.library_box.setCurrentIndex(self.library_box.count() - 1)

    def _fits_changed(self, key: str) -> None:
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._spectrum_changed()

    def _set_status(self, text: str, ok: bool) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {'#0b5cad' if ok else '#b00020'}; font-weight: bold;")

    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self.result = None
        self.candidate_table.set_table([], [])
        self.evidence_table.set_rows([])
        self._set_status("", True)
        self.export_button.setEnabled(False)
        self.efficiency_check.setEnabled(self.state.efficiency is not None)
        if self.state.efficiency is None:
            self.efficiency_check.setChecked(False)
        if s is None:
            self.gate.setText("Load a dataset first.")
            self.run_button.setEnabled(False)
            return
        missing = list(ids.missing_inputs(s, self.current_library()))
        if not self.state.fits(s.data_sha256):
            missing.append("fitted peaks: fit the peaks of this spectrum on the Peak Fitting page")
        if missing:
            self.gate.setText(
                "<b>Not available.</b> Missing:<ul>"
                + "".join(f"<li>{m}</li>" for m in missing)
                + "</ul>"
            )
            self.gate.setStyleSheet("color: #b00020;")
        else:
            self.gate.setText(
                "Lines are judged only against the fitted peaks: fit every peak of the "
                "spectrum first, otherwise their lines count as missing."
            )
            self.gate.setStyleSheet("")
        self.run_button.setEnabled(not missing)

    # ------------------------------------------------------------------ actions
    def run(self) -> None:
        s = self.current_spectrum()
        if s is None:
            return
        curve = (
            self.state.efficiency[1]
            if self.efficiency_check.isChecked() and self.state.efficiency is not None
            else None
        )
        try:
            settings = ids.IdentificationSettings(
                k=self.k_box.value(), extra_energy_unc_kev=self.extra_box.value()
            )
            result = ids.identify(
                s, self.state.fits(s.data_sha256), self.current_library(), settings,
                efficiency=curve,
            )  # fmt: skip
        except (ValueError, ids.ProcessingError) as exc:
            self.result = None
            self.candidate_table.set_table([], [])
            self.evidence_table.set_rows([])
            self._set_status(f"Ranking refused: {exc}", False)
            return
        self.result = result
        self.candidate_table.set_table(*ids.candidate_table(result))
        n = len(result.candidates)
        self._set_status(
            f"{ids.result_label(s)} — {n} candidate(s); "
            f"{len(result.unexplained_peaks)} peak(s) explained by no library line",
            True,
        )
        self.export_button.setEnabled(True)
        if n:
            self.candidate_table.selectRow(0)

    def _candidate_selected(self) -> None:
        s = self.current_spectrum()
        rows = self.candidate_table.selectionModel().selectedRows()
        if self.result is None or s is None or not rows:
            return
        candidate = self.result.candidates[rows[0].row()]
        self.evidence_table.set_rows(ids.evidence_rows(s, self.result, candidate))

    def suggested_filename(self) -> str:
        s = self.current_spectrum()
        stem = "spectrum"
        if s is not None:
            stem = Path(s.source.filename).stem if s.source else (s.synthetic_id or stem)
        return f"{stem}_nuclide_candidates.csv"
