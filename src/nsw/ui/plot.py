"""Interactive spectrum plot (PyQtGraph). Display only: no analysis happens here.

The x-axis is always the 1-based channel number. Energy labels are never shown by this
widget in the current phase, so an unverified calibration cannot leak onto the screen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from nsw.core.calibration import CalibrationStatus
from nsw.core.spectrum import Spectrum
from nsw.services.export import CALIBRATION_WARNING, SYNTHETIC_NOTICE

COLOURS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#7f7f7f")
MAX_OVERLAY = len(COLOURS)
LOG_FLOOR = 0.5  # where empty channels are drawn on a log axis (display only)


@dataclass(frozen=True)
class PlotItemSpec:
    spectrum: Spectrum
    label: str
    scale: float = 1.0  # display-only multiplier (ignored in count-rate mode)


class SpectrumPlot(QWidget):
    """Step plot of one or more spectra with channel cursor, zoom and pan."""

    cursor_moved = Signal(int)  # channel under the cursor

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[PlotItemSpec] = []
        self._cursor_channel: int | None = None
        self._marker_lines: list[pg.InfiniteLine] = []

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setObjectName("calibrationBanner")

        self.log_box = QCheckBox("Logarithmic counts axis")
        self.rate_box = QCheckBox("Show count rate (per live second)")
        self.rate_box.setToolTip(
            "Divide by the recorded live time so that spectra of different duration can be "
            "compared. Display only."
        )
        self.reset_button = QPushButton("Reset view")
        self.readout = QLabel("Move the mouse over the plot to read channel contents.")
        self.readout.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        pg.setConfigOptions(antialias=False)
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "Channel")
        self.plot.setMouseEnabled(x=True, y=True)  # drag = pan, wheel = zoom
        self.legend = self.plot.addLegend(offset=(-10, 10))
        self.cursor_line = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#555555", width=1)
        )
        self.cursor_line.setZValue(10)
        self.plot.addItem(self.cursor_line, ignoreBounds=True)
        self.cursor_line.hide()
        self._proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved
        )

        controls = QHBoxLayout()
        controls.addWidget(self.log_box)
        controls.addWidget(self.rate_box)
        controls.addStretch(1)
        controls.addWidget(self.reset_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        layout.addLayout(controls)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.readout)

        self.log_box.toggled.connect(self._redraw)
        self.rate_box.toggled.connect(self._redraw)
        self.reset_button.clicked.connect(self.reset_view)
        self._update_banner()

    # ------------------------------------------------------------------ public API
    def set_spectra(self, items: list[PlotItemSpec]) -> None:
        self._items = list(items[:MAX_OVERLAY])
        self._redraw()
        self.reset_view()

    def items(self) -> list[PlotItemSpec]:
        return list(self._items)

    def set_markers(self, channels: Sequence[float]) -> None:
        """Mark channel positions (e.g. peak candidates) with thin vertical lines."""
        for line in self._marker_lines:
            self.plot.removeItem(line)
        self._marker_lines = []
        pen = pg.mkPen((230, 126, 0, 170), width=1)
        for ch in channels:
            line = pg.InfiniteLine(pos=float(ch), angle=90, movable=False, pen=pen)
            line.setZValue(5)
            self.plot.addItem(line, ignoreBounds=True)
            self._marker_lines.append(line)

    def markers(self) -> list[float]:
        return [float(line.value()) for line in self._marker_lines]

    def zoom_to(self, first_channel: float, last_channel: float) -> None:
        self.plot.getPlotItem().vb.setXRange(first_channel, last_channel, padding=0.02)

    def reset_view(self) -> None:
        view = self.plot.getPlotItem().vb
        view.enableAutoRange()
        view.autoRange()

    def x_axis_label(self) -> str:
        return str(self.plot.getAxis("bottom").labelText)

    def y_axis_label(self) -> str:
        return str(self.plot.getAxis("left").labelText)

    def displayed_y(self, index: int) -> NDArray[np.float64]:
        """The y values currently drawn for item ``index`` (after display options)."""
        return self._display_values(self._items[index])

    def set_cursor_channel(self, channel: int) -> None:
        """Place the cursor on a channel and update the readout."""
        if not self._items:
            return
        s0 = self._items[0].spectrum
        channel = int(np.clip(channel, s0.first_channel, s0.first_channel + s0.n_channels - 1))
        self._cursor_channel = channel
        self.cursor_line.setPos(channel)
        self.cursor_line.show()
        parts = [f"Channel {channel}"]
        for item in self._items:
            s = item.spectrum
            i = channel - s.first_channel
            if not 0 <= i < s.n_channels:
                parts.append(f"{item.label}: outside range")
                continue
            value = float(s.counts[i])
            unc = float(s.uncertainty_array()[i])
            text = f"{value:,.0f}" if s.is_count_data else f"{value:,.2f}"
            parts.append(f"{item.label}: {text} ± {unc:,.2f} counts")
        self.readout.setText("    |    ".join(parts))
        self.cursor_moved.emit(channel)

    # ------------------------------------------------------------------ internals
    def _display_values(self, item: PlotItemSpec) -> NDArray[np.float64]:
        s = item.spectrum
        y = s.counts.astype(np.float64)
        if self.rate_box.isChecked() and s.live_time_s:
            return np.asarray(y / s.live_time_s, dtype=np.float64)
        return np.asarray(y * item.scale, dtype=np.float64)

    def _redraw(self) -> None:
        for curve in [i for i in self.plot.listDataItems()]:
            self.plot.removeItem(curve)
        self.legend.clear()
        log = self.log_box.isChecked()
        rate = self.rate_box.isChecked()
        self.plot.setLogMode(x=False, y=log)
        for n, item in enumerate(self._items):
            s = item.spectrum
            y = self._display_values(item)
            if log:
                floor = LOG_FLOOR / s.live_time_s if rate and s.live_time_s else LOG_FLOOR
                y = np.where(y > 0, y, floor)
            edges = np.arange(s.first_channel - 0.5, s.first_channel + s.n_channels + 0.5, 1.0)
            self.plot.plot(
                edges, y, stepMode="center", pen=pg.mkPen(COLOURS[n], width=1), name=item.label
            )
        unit = "Counts per channel per live second" if rate else "Counts per channel"
        if log:
            unit += "  (empty channels drawn at the axis floor)"
        self.plot.setLabel("left", unit)
        self.plot.setLabel("bottom", "Channel")
        self._update_banner()
        if self._cursor_channel is not None and self._items:
            self.set_cursor_channel(self._cursor_channel)

    def _update_banner(self) -> None:
        spectra = [i.spectrum for i in self._items]
        lines: list[str] = []
        colour = "#b00020"
        if any(s.calibration_status is not CalibrationStatus.VERIFIED for s in spectra):
            lines.append(CALIBRATION_WARNING)
        if any(s.is_synthetic for s in spectra):
            lines.append(SYNTHETIC_NOTICE)
            if len(lines) == 1:
                colour = "#0b5cad"
        if not spectra:
            lines, colour = ["No spectrum selected."], "#555555"
        self.banner.setText("\n".join(lines))
        self.banner.setStyleSheet(
            f"QLabel#calibrationBanner {{ color: white; background: {colour}; "
            "font-weight: bold; padding: 6px; }}"
        )

    def _on_mouse_moved(self, event: tuple[object, ...]) -> None:
        pos = event[0]
        if not self._items or not self.plot.sceneBoundingRect().contains(pos):
            return
        x = self.plot.getPlotItem().vb.mapSceneToView(pos).x()
        self.set_cursor_channel(round(x))
