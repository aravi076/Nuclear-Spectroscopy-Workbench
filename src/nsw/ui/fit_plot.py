"""Fit display: data with model overlay, and normalised residuals below. Display only.

The x-axis is the 1-based channel number, as everywhere in the viewer (TD-016).
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from nsw.services.peaks import FitResult

STATUS_COLOURS = {"synthetic": "#0b5cad", "provisional": "#b00020", "plain": "#1b7f3b"}


def link_x(follower: pg.PlotWidget, leader: pg.PlotWidget) -> None:
    """Link the x-axis of ``follower`` to ``leader`` with automatic ranging switched off
    on both. Two linked views that both auto-range keep correcting each other (their
    axis areas differ in width), which floods the event loop with layout updates."""
    leader.getPlotItem().vb.disableAutoRange()
    follower.getPlotItem().vb.disableAutoRange()
    follower.setXLink(leader)


def show_all(leader: pg.PlotWidget, follower: pg.PlotWidget) -> None:
    """One-shot ranging after new data: full range on the leader, y range on the follower
    (its x range follows through the link)."""
    leader.getPlotItem().vb.autoRange()
    view = follower.getPlotItem().vb
    bounds = view.childrenBounds(frac=(1.0, 1.0))
    y = bounds[1]
    if y is not None and y[1] > y[0]:
        view.setYRange(float(y[0]), float(y[1]), padding=0.1)


class FitPlot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._result: FitResult | None = None
        self.banner = QLabel("No fit.")
        self.banner.setWordWrap(True)
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setObjectName("fitBanner")

        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("left", "Counts per channel")
        self.legend = self.plot.addLegend(offset=(-10, 10))
        self.residual_plot = pg.PlotWidget(background="w")
        self.residual_plot.showGrid(x=True, y=True, alpha=0.2)
        self.residual_plot.setLabel("left", "Residual / σ")
        self.residual_plot.setLabel("bottom", "Channel")
        link_x(self.residual_plot, self.plot)
        self.residual_plot.setMaximumHeight(170)
        for level, style in ((0.0, Qt.PenStyle.SolidLine), (2.0, Qt.PenStyle.DashLine)):
            for sign in (1.0, -1.0) if level else (1.0,):
                self.residual_plot.addItem(
                    pg.InfiniteLine(
                        pos=sign * level, angle=0, pen=pg.mkPen("#888888", width=1, style=style)
                    ),
                    ignoreBounds=True,
                )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.banner)
        layout.addWidget(self.plot, 3)
        layout.addWidget(self.residual_plot, 1)
        self._set_banner("No fit.", "#555555")

    def _set_banner(self, text: str, colour: str) -> None:
        self.banner.setText(text)
        self.banner.setStyleSheet(
            f"QLabel#fitBanner {{ color: white; background: {colour}; font-weight: bold; "
            "padding: 6px; }}"
        )

    def clear(self) -> None:
        self._result = None
        for plot in (self.plot, self.residual_plot):
            for item in list(plot.listDataItems()):
                plot.removeItem(item)
        self.legend.clear()
        self._set_banner("No fit.", "#555555")

    def set_result(self, result: FitResult, status: str) -> None:
        self.clear()
        self._result = result
        x = result.channels
        edges = np.append(x - 0.5, x[-1] + 0.5)
        label = "gross counts" if result.method == "joint-poisson" else "data"
        self.plot.plot(edges, result.data, stepMode="center", pen=pg.mkPen("#222222"), name=label)
        self.plot.plot(x, result.fitted, pen=pg.mkPen("#d62728", width=2), name="fitted model")
        if result.method != "joint-poisson":
            self.plot.plot(
                x,
                result.fitted_continuum,
                pen=pg.mkPen("#2ca02c", width=1, style=Qt.PenStyle.DashLine),
                name="continuum (and step)",
            )
        self.residual_plot.plot(
            x, result.residuals, pen=None, symbol="o", symbolSize=5, symbolBrush="#1f77b4"
        )
        self.plot.setLabel("bottom", "Channel")
        show_all(self.plot, self.residual_plot)
        kind = (
            "synthetic"
            if status.startswith("SYNTHETIC")
            else ("provisional" if "PROVISIONAL" in status else "plain")
        )
        self._set_banner(status, STATUS_COLOURS[kind])

    def result(self) -> FitResult | None:
        return self._result

    def curve(self, index: int) -> NDArray[np.float64]:
        """y data of curve ``index`` in the upper plot (0 data, 1 model, 2 continuum)."""
        return np.asarray(self.plot.listDataItems()[index].yData, dtype=np.float64)

    def residual_points(self) -> NDArray[np.float64]:
        return np.asarray(self.residual_plot.listDataItems()[0].yData, dtype=np.float64)
