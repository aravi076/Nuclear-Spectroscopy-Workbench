"""Resolution Analysis page: FWHM versus channel (or energy) from fitted peaks.

Calculations are in ``nsw.services.resolution``; the fit is instantaneous and runs on
the GUI thread.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nsw.core.spectrum import Spectrum
from nsw.services import resolution as res_service
from nsw.ui.explorer import KeyValueTable
from nsw.ui.fit_plot import link_x, show_all
from nsw.ui.peak_search_page import describe_entry
from nsw.ui.state import AppState, Entry
from nsw.ui.tables import DataTable

HEADERS = ["Use", "Centroid (ch)", "FWHM (ch)", "±", "Area"]
VARIABLE_LABELS = {
    res_service.ResolutionVariable.CHANNEL: "Channel (FWHM in channels)",
    res_service.ResolutionVariable.ENERGY_KEV: "Energy in keV (FWHM in keV)",
}
NO_PEAKS_HINT = (
    "No acceptable fitted peaks for this spectrum. Fit peaks on the Peak Fitting page first. "
    "Only peaks of statistically acceptable fits with a fitted (not fixed) width are offered."
)
USE_HINT = (
    "Untick peaks that are not single full-energy lines (multiplets, annihilation or other "
    "broadened peaks): their width is not a measure of the detector resolution."
)


class ResolutionPlot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.residual_plot = pg.PlotWidget(background="w")
        self.residual_plot.showGrid(x=True, y=True, alpha=0.2)
        self.residual_plot.setLabel("left", "Residual / σ")
        link_x(self.residual_plot, self.plot)
        self.residual_plot.addItem(
            pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen("#888888", width=1)), ignoreBounds=True
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot, 3)
        layout.addWidget(self.residual_plot, 2)
        self._model: NDArray[np.float64] = np.zeros(0)

    def clear(self) -> None:
        for plot in (self.plot, self.residual_plot):
            for item in list(plot.listDataItems()):
                plot.removeItem(item)
            for item in [i for i in plot.items() if isinstance(i, pg.ErrorBarItem)]:
                plot.removeItem(item)
        self._model = np.zeros(0)

    def set_fit(self, fit: res_service.ResolutionFit) -> None:
        self.clear()
        x = np.array([p.x for p in fit.points])
        w = np.array([p.fwhm for p in fit.points])
        u = np.array([p.fwhm_unc for p in fit.points])
        cx, cw, cu = res_service.curve(fit)
        red = pg.mkPen("#d62728", width=2)
        dotted = pg.mkPen("#d62728", width=1, style=Qt.PenStyle.DotLine)
        self.plot.plot(cx, cw, pen=red)
        self.plot.plot(cx, cw + cu, pen=dotted)
        self.plot.plot(cx, cw - cu, pen=dotted)
        self.plot.addItem(
            pg.ErrorBarItem(x=x, y=w, height=2.0 * u, beam=0.0, pen=pg.mkPen("#1f77b4"))
        )
        self.plot.plot(x, w, pen=None, symbol="o", symbolSize=7, symbolBrush="#1f77b4")
        self.residual_plot.plot(
            x, fit.normalised_residuals, pen=None, symbol="o", symbolSize=7, symbolBrush="#1f77b4"
        )
        energy = fit.variable is res_service.ResolutionVariable.ENERGY_KEV
        self.plot.setLabel("left", f"FWHM ({fit.unit})")
        self.residual_plot.setLabel("bottom", "Energy (keV)" if energy else "Channel")
        show_all(self.plot, self.residual_plot)
        self._model = np.asarray(cw, dtype=np.float64)

    def model_curve(self) -> NDArray[np.float64]:
        return self._model


class ResolutionPage(QWidget):
    export_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._entries: list[Entry] = []
        self._rows: list[res_service.PeakRow] = []
        self.fit: res_service.ResolutionFit | None = None

        self.spectrum_box = QComboBox()
        self.spectrum_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.spectrum_box.setMinimumContentsLength(40)
        self.variable_box = QComboBox()
        self.quadratic_check = QCheckBox("Include the c·x² term")
        self.quadratic_check.setToolTip(
            "Needs at least three peaks. Use it only if the residuals of the two-term model "
            "show a systematic trend; for HPGe detectors it is usually not needed."
        )
        self.fit_button = QPushButton("Fit resolution model")
        self.export_button = QPushButton("Export resolution as CSV…")
        form = QFormLayout()
        form.addRow("Spectrum:", self.spectrum_box)
        form.addRow("Abscissa:", self.variable_box)
        form.addRow("", self.quadratic_check)
        buttons = QHBoxLayout()
        buttons.addWidget(self.fit_button)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.peak_table = QTableWidget(0, len(HEADERS))
        self.peak_table.setHorizontalHeaderLabels(HEADERS)
        self.peak_table.verticalHeader().setVisible(False)
        self.peak_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.plot = ResolutionPlot()
        self.result_table = KeyValueTable()
        self.point_table = DataTable()
        tabs = QTabWidget()
        tabs.addTab(self.result_table, "Result")
        tabs.addTab(self.point_table, "Points and residuals")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(form)
        left_layout.addWidget(self.hint)
        left_layout.addWidget(self.peak_table, 1)
        left_layout.addLayout(buttons)
        left_layout.addWidget(self.status)
        upper = QSplitter(Qt.Orientation.Horizontal)
        upper.addWidget(left)
        upper.addWidget(self.plot)
        upper.setStretchFactor(0, 2)
        upper.setStretchFactor(1, 3)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(upper)
        split.addWidget(tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        QVBoxLayout(self).addWidget(split)

        self.fit_button.clicked.connect(self.run)
        self.export_button.clicked.connect(self.export_requested)
        self.spectrum_box.currentIndexChanged.connect(self._spectrum_changed)
        state.changed.connect(self.refresh)
        state.fits_changed.connect(self._fits_changed)
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

    def _fits_changed(self, key: str) -> None:
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._spectrum_changed()

    def _set_status(self, text: str, colour: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-weight: bold;")

    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self.fit = None
        self.plot.clear()
        self.result_table.set_rows([])
        self.point_table.set_table([], [])
        self.variable_box.clear()
        if s is not None:
            for variable in res_service.available_variables(s):
                self.variable_box.addItem(VARIABLE_LABELS[variable], variable.value)
        self._rows = res_service.usable_rows(self.state.fits(s.data_sha256)) if s else []
        self.peak_table.setRowCount(len(self._rows))
        read_only = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for r, row in enumerate(self._rows):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Checked)
            self.peak_table.setItem(r, 0, use)
            for col, text in enumerate(
                (
                    f"{row.centroid:.3f}",
                    f"{row.fwhm:.3f}",
                    f"{row.fwhm_unc:.3f}",
                    f"{row.area:.0f}",
                ),
                start=1,
            ):
                item = QTableWidgetItem(text)
                item.setFlags(read_only)
                self.peak_table.setItem(r, col, item)
        if s is None:
            self.hint.setText("Load a dataset first.")
        else:
            self.hint.setText(USE_HINT if self._rows else NO_PEAKS_HINT)
        self._set_status("", "#555555")
        self.fit_button.setEnabled(len(self._rows) >= 2)
        self.export_button.setEnabled(False)

    # ------------------------------------------------------------------ selection
    def set_used(self, row: int, used: bool) -> None:
        item = self.peak_table.item(row, 0)
        assert item is not None
        item.setCheckState(Qt.CheckState.Checked if used else Qt.CheckState.Unchecked)

    def used_rows(self) -> list[res_service.PeakRow]:
        out = []
        for r, row in enumerate(self._rows):
            item = self.peak_table.item(r, 0)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                out.append(row)
        return out

    # ------------------------------------------------------------------ action
    def run(self) -> None:
        s = self.current_spectrum()
        if s is None:
            return
        try:
            variable = res_service.ResolutionVariable(self.variable_box.currentData())
            fit = res_service.analyse(
                s, self.used_rows(), variable, self.quadratic_check.isChecked()
            )
        except (ValueError, res_service.ProcessingError) as exc:
            self.fit = None
            self.plot.clear()
            self.result_table.set_rows([])
            self.point_table.set_table([], [])
            self.export_button.setEnabled(False)
            self._set_status(f"Resolution fit refused: {exc}", "#b00020")
            return
        self.fit = fit
        self.plot.set_fit(fit)
        self.result_table.set_rows(res_service.fit_rows(s, fit))
        self.point_table.set_table(*res_service.point_table(fit))
        verified, _ = res_service.verification(s, fit)
        text = res_service.status_text(s, fit)
        if fit.poor_fit:
            text += "   The widths are not consistent with the model."
        self._set_status(text, "#0b5cad" if verified else "#b00020")
        self.export_button.setEnabled(True)

    def suggested_filename(self) -> str:
        s = self.current_spectrum()
        if s is None:
            return "resolution.csv"
        stem = Path(s.source.filename).stem if s.source else (s.synthetic_id or "spectrum")
        return f"{stem}_resolution.csv"
