"""Energy Calibration page: assign reference energies to fitted peaks and fit E(channel).

All calculations are in ``nsw.services.calibration``. The regression is instantaneous,
so it runs on the GUI thread.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
from nsw.services import calibration as cal_service
from nsw.ui.explorer import KeyValueTable
from nsw.ui.fit_plot import link_x, show_all
from nsw.ui.peak_search_page import describe_entry
from nsw.ui.state import AppState, Entry
from nsw.ui.tables import DataTable

COL_USE, COL_CHANNEL, COL_UNC, COL_AREA, COL_FWHM, COL_QUALITY = 0, 1, 2, 3, 4, 5
COL_ENERGY, COL_ENERGY_UNC, COL_SOURCE, COL_LABEL = 6, 7, 8, 9
HEADERS = [
    "Use",
    "Centroid (ch)",
    "±",
    "Area",
    "FWHM (ch)",
    "Fit quality",
    "Reference energy (keV)",
    "± (keV)",
    "Source of the reference value",
    "Label",
]
NO_PEAKS_HINT = (
    "No fitted peaks for this spectrum. Fit peaks on the Peak Fitting page first; they "
    "appear here as possible calibration points."
)
ASSIGN_HINT = (
    "Tick the peaks to use and enter for each its reference energy, the uncertainty of "
    "that energy and where the value comes from. NSW does not supply reference energies."
)


class CalibrationPlot(QWidget):
    """Calibration curve with points (top) and residuals in keV (bottom)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.curve_plot = pg.PlotWidget(background="w")
        self.curve_plot.showGrid(x=True, y=True, alpha=0.2)
        self.curve_plot.setLabel("left", "Reference energy (keV)")
        self.residual_plot = pg.PlotWidget(background="w")
        self.residual_plot.showGrid(x=True, y=True, alpha=0.2)
        self.residual_plot.setLabel("left", "Reference − fitted (keV)")
        self.residual_plot.setLabel("bottom", "Channel")
        link_x(self.residual_plot, self.curve_plot)
        self.residual_plot.addItem(
            pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen("#888888", width=1)),
            ignoreBounds=True,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.curve_plot, 3)
        layout.addWidget(self.residual_plot, 2)
        self._residuals: NDArray[np.float64] = np.zeros(0)

    def clear(self) -> None:
        for plot in (self.curve_plot, self.residual_plot):
            for item in list(plot.listDataItems()):
                plot.removeItem(item)
            for item in [i for i in plot.items() if isinstance(i, pg.ErrorBarItem)]:
                plot.removeItem(item)
        self._residuals = np.zeros(0)

    def set_fit(self, fit: cal_service.CalibrationFit) -> None:
        self.clear()
        x = np.array([p.channel for p in fit.points])
        y = np.array([p.reference_value for p in fit.points])
        ch, e, u = cal_service.curve(fit)
        self.curve_plot.plot(ch, e, pen=pg.mkPen("#d62728", width=2))
        self.curve_plot.plot(ch, e + u, pen=pg.mkPen("#d62728", width=1, style=Qt.PenStyle.DotLine))
        self.curve_plot.plot(ch, e - u, pen=pg.mkPen("#d62728", width=1, style=Qt.PenStyle.DotLine))
        self.curve_plot.plot(x, y, pen=None, symbol="o", symbolSize=7, symbolBrush="#1f77b4")
        self.residual_plot.addItem(
            pg.ErrorBarItem(
                x=x,
                y=fit.residuals_kev,
                height=2.0 * fit.effective_uncertainties_kev,
                beam=0.0,
                pen=pg.mkPen("#1f77b4"),
            )
        )
        self.residual_plot.plot(
            x, fit.residuals_kev, pen=None, symbol="o", symbolSize=7, symbolBrush="#1f77b4"
        )
        self._residuals = np.asarray(fit.residuals_kev, dtype=float)
        show_all(self.curve_plot, self.residual_plot)

    def residuals(self) -> NDArray[np.float64]:
        return self._residuals


class CalibrationPage(QWidget):
    export_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._entries: list[Entry] = []
        self._rows: list[cal_service.PeakRow] = []
        self.fit: cal_service.CalibrationFit | None = None
        self.record: cal_service.EnergyCalibration | None = None
        self.comparison: cal_service.OrderComparison | None = None
        self.stability: cal_service.StabilityResult | None = None

        self.spectrum_box = QComboBox()
        self.spectrum_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.spectrum_box.setMinimumContentsLength(40)
        self.order_box = QComboBox()
        self.order_box.addItem("Linear: E = c0 + c1·ch", 1)
        self.order_box.addItem("Quadratic: E = c0 + c1·ch + c2·ch²", 2)
        self.inflate_check = QCheckBox("Multiply covariance by χ²/ndf if χ²/ndf > 1")
        self.inflate_check.setToolTip(
            "Use only when the scatter of the points is real (for example electronic "
            "non-linearity) and not a wrong line assignment. The choice is recorded."
        )
        self.fit_button = QPushButton("Fit calibration")
        self.order_button = QPushButton("Test next higher order")
        self.export_button = QPushButton("Export calibration as CSV…")

        form = QFormLayout()
        form.addRow("Spectrum:", self.spectrum_box)
        form.addRow("Model:", self.order_box)
        form.addRow("", self.inflate_check)
        buttons = QHBoxLayout()
        for b in (self.fit_button, self.order_button, self.export_button):
            buttons.addWidget(b)
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
        self.peak_table.horizontalHeader().setSectionResizeMode(
            COL_SOURCE, QHeaderView.ResizeMode.Stretch
        )
        self.plot = CalibrationPlot()

        self.result_table = KeyValueTable()
        self.point_table = DataTable()
        self.history_table = DataTable()
        self.stability_channel = QDoubleSpinBox()
        self.stability_channel.setRange(1.0, 65536.0)
        self.stability_channel.setDecimals(1)
        self.stability_tolerance = QDoubleSpinBox()
        self.stability_tolerance.setRange(0.1, 100.0)
        self.stability_tolerance.setValue(3.0)
        self.stability_button = QPushButton("Compare this peak position across spectra")
        self.stability_status = QLabel()
        self.stability_status.setWordWrap(True)
        self.stability_table = DataTable()
        stability = QWidget()
        s_layout = QVBoxLayout(stability)
        s_row = QHBoxLayout()
        s_row.addWidget(QLabel("Peak near channel:"))
        s_row.addWidget(self.stability_channel)
        s_row.addWidget(QLabel("within ± channels:"))
        s_row.addWidget(self.stability_tolerance)
        s_row.addWidget(self.stability_button)
        s_row.addStretch(1)
        s_layout.addLayout(s_row)
        s_layout.addWidget(self.stability_status)
        s_layout.addWidget(self.stability_table, 1)
        tabs = QTabWidget()
        tabs.addTab(self.result_table, "Result")
        tabs.addTab(self.point_table, "Points and residuals")
        tabs.addTab(self.history_table, "Calibration history (this session)")
        tabs.addTab(stability, "Stability of a peak position")
        self.tabs = tabs

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
        upper.setStretchFactor(0, 3)
        upper.setStretchFactor(1, 2)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(upper)
        split.addWidget(tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        QVBoxLayout(self).addWidget(split)

        self.fit_button.clicked.connect(self.run)
        self.order_button.clicked.connect(self.test_order)
        self.export_button.clicked.connect(self.export_requested)
        self.stability_button.clicked.connect(self.run_stability)
        self.spectrum_box.currentIndexChanged.connect(self._spectrum_changed)
        state.changed.connect(self.refresh)
        state.fits_changed.connect(self._fits_changed)
        state.calibrations_changed.connect(self._show_history)
        self.refresh()
        self._show_history()

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

    def _fits_changed(self, key: str) -> None:
        s = self.current_spectrum()
        if s is not None and s.data_sha256 == key:
            self._spectrum_changed()

    def _spectrum_changed(self) -> None:
        s = self.current_spectrum()
        self.fit = None
        self.record = None
        self.comparison = None
        self.plot.clear()
        self.result_table.set_rows([])
        self.point_table.set_table([], [])
        self._rows = cal_service.peak_rows(self.state.fits(s.data_sha256)) if s else []
        self._fill_peak_table()
        if s is None:
            self.hint.setText("Load a dataset first.")
        else:
            self.hint.setText(ASSIGN_HINT if self._rows else NO_PEAKS_HINT)
        self._set_status("", "#555555")
        self._update_enabled()

    def _fill_peak_table(self) -> None:
        table = self.peak_table
        table.setRowCount(len(self._rows))
        read_only = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for r, row in enumerate(self._rows):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Unchecked)
            table.setItem(r, COL_USE, use)
            for col, text in (
                (COL_CHANNEL, f"{row.centroid:.3f}"),
                (COL_UNC, f"{row.centroid_unc:.3f}"),
                (COL_AREA, f"{row.area:.0f}"),
                (COL_FWHM, f"{row.fwhm:.2f}"),
                (COL_QUALITY, row.quality),
            ):
                item = QTableWidgetItem(text)
                item.setFlags(read_only)
                table.setItem(r, col, item)
            for col in (COL_ENERGY, COL_ENERGY_UNC, COL_SOURCE, COL_LABEL):
                table.setItem(r, col, QTableWidgetItem(""))

    def _update_enabled(self) -> None:
        has_rows = bool(self._rows)
        self.fit_button.setEnabled(has_rows)
        self.order_button.setEnabled(has_rows)
        self.export_button.setEnabled(self.fit is not None)

    # ------------------------------------------------------------------ assignment
    def set_reference(
        self, row: int, energy_kev: float, uncertainty_kev: float, source: str, label: str = ""
    ) -> None:
        """Fill and tick one row (what the analyst does by typing into the table)."""
        self._cell(row, COL_ENERGY).setText(repr(energy_kev))
        self._cell(row, COL_ENERGY_UNC).setText(repr(uncertainty_kev))
        self._cell(row, COL_SOURCE).setText(source)
        self._cell(row, COL_LABEL).setText(label)
        self._cell(row, COL_USE).setCheckState(Qt.CheckState.Checked)

    def _cell(self, row: int, column: int) -> QTableWidgetItem:
        item = self.peak_table.item(row, column)
        assert item is not None  # every cell is created in _fill_peak_table
        return item

    def row_near(self, channel: float) -> int:
        """Index of the table row whose centroid is nearest to ``channel``."""
        return int(np.argmin([abs(r.centroid - channel) for r in self._rows]))

    def points(self) -> list[cal_service.CalibrationPoint]:
        """Reference points from the ticked rows. Raises ValueError / ProcessingError."""
        out = []
        for r, row in enumerate(self._rows):
            if self._cell(r, COL_USE).checkState() is not Qt.CheckState.Checked:
                continue
            try:
                energy = float(self._cell(r, COL_ENERGY).text())
                unc_text = self._cell(r, COL_ENERGY_UNC).text().strip()
                unc = float(unc_text) if unc_text else 0.0
            except ValueError as exc:
                raise ValueError(
                    f"row {r + 1}: the reference energy and its uncertainty must be numbers"
                ) from exc
            out.append(
                cal_service.make_point(
                    row,
                    energy,
                    unc,
                    self._cell(r, COL_SOURCE).text(),
                    self._cell(r, COL_LABEL).text(),
                )
            )
        return out

    # ------------------------------------------------------------------ actions
    def run(self) -> None:
        s = self.current_spectrum()
        if s is None:
            return
        try:
            points = self.points()
            if not points:
                raise ValueError("no peak is ticked")
            fit = cal_service.calibrate(
                points, self.order_box.currentData(), self.inflate_check.isChecked()
            )
        except (ValueError, cal_service.ProcessingError) as exc:
            self.fit = None
            self.record = None
            self.plot.clear()
            self.result_table.set_rows([])
            self.point_table.set_table([], [])
            self._set_status(f"Calibration refused: {exc}", "#b00020")
            self._update_enabled()
            return
        now = datetime.now().astimezone()
        self.fit = fit
        self.record = cal_service.make_record(s, fit, now)
        self.plot.set_fit(fit)
        self.result_table.set_rows(cal_service.fit_rows(s, fit))
        self.point_table.set_table(*cal_service.point_table(fit))
        verified, _ = cal_service.verification(s, fit)
        self._set_status(
            cal_service.status_text(s, fit),
            "#0b5cad" if (s.is_synthetic and verified) else "#b00020",
        )
        self.state.add_calibration(
            cal_service.CalibrationHistoryItem(
                now, describe_entry(self._entries[self.spectrum_box.currentIndex()]),
                s.is_synthetic, self.record, fit, s.data_sha256,
            )
        )  # fmt: skip
        self._update_enabled()

    def test_order(self) -> None:
        try:
            points = self.points()
            if not points:
                raise ValueError("no peak is ticked")
            self.comparison = cal_service.order_test(points, self.order_box.currentData())
        except (ValueError, cal_service.ProcessingError) as exc:
            self.comparison = None
            self._set_status(f"Order test refused: {exc}", "#b00020")
            return
        self._set_status(
            self.comparison.statement, "#b26a00" if self.comparison.justified else "#1b7f3b"
        )

    def _show_history(self) -> None:
        self.history_table.set_table(*cal_service.history_table(self.state.calibration_history))

    def run_stability(self) -> None:
        pairs = [(e.spectrum, self.state.fits(e.key)) for e in self.state.all_entries()]
        pairs = [(s, fits) for s, fits in pairs if fits]
        try:
            result, table = cal_service.stability_of_line(
                pairs, self.stability_channel.value(), self.stability_tolerance.value()
            )
        except cal_service.ProcessingError as exc:
            self.stability = None
            self.stability_table.set_table([], [])
            self.stability_status.setText(f"Not possible: {exc}")
            self.stability_status.setStyleSheet("color: #b00020; font-weight: bold;")
            return
        self.stability = result
        self.stability_table.set_table(*table)
        self.stability_status.setText(result.statement)
        colour = "#1b7f3b" if result.stable else "#b00020"
        self.stability_status.setStyleSheet(f"color: {colour}; font-weight: bold;")

    def suggested_filename(self) -> str:
        s = self.current_spectrum()
        if s is None:
            return "calibration.csv"
        stem = Path(s.source.filename).stem if s.source else (s.synthetic_id or "spectrum")
        return f"{stem}_energy_calibration.csv"
