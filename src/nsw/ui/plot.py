"""Interactive spectrum plot (PyQtGraph). Display only: no analysis happens here.

The x-axis is always the 1-based channel number. Energy labels are never shown by this
widget in the current phase, so an unverified calibration cannot leak onto the screen.

Interaction modes (docs/design/UX_REDESIGN_PLAN.md §3), switched with the toolbar or keys:

    Navigate (Esc)   drag = pan, wheel = zoom about the cursor, double-click = reset
    Zoom box (Z)     drag a rectangle to zoom to it
    Region (R)       drag along x to draw a region of interest; edges and body movable
    Select (S)       click a peak marker to select it, Ctrl+click to add or remove

Always: Home reset view, L log axis, Del delete the selected region, F fit request,
Ctrl+Z / Ctrl+Y undo / redo request, right-click context menu. The plot only *reports*
what the analyst did (signals); the owner of the regions (``RoiController``) validates,
applies and records it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nsw.core.calibration import CalibrationStatus
from nsw.core.spectrum import Spectrum
from nsw.services.export import CALIBRATION_WARNING, SYNTHETIC_NOTICE

COLOURS = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#7f7f7f")
MAX_OVERLAY = len(COLOURS)
LOG_FLOOR = 0.5  # where empty channels are drawn on a log axis (display only)
MARKER_PICK_PIXELS = 6  # a click within this distance of a marker selects it
ROI_BRUSH = (31, 119, 180, 40)
ROI_SELECTED_BRUSH = (214, 39, 40, 55)
MARKER_PEN = (230, 126, 0, 170)
MARKER_SELECTED_PEN = (214, 39, 40)


class PlotMode(StrEnum):
    NAVIGATE = "navigate"
    ZOOM_BOX = "zoom box"
    REGION = "region"
    SELECT = "select"


MODE_HELP = {
    PlotMode.NAVIGATE: ("Navigate", "Esc", "drag to pan, wheel to zoom, double-click to reset"),
    PlotMode.ZOOM_BOX: ("Zoom box", "Z", "drag a rectangle to zoom to it"),
    PlotMode.REGION: ("Region", "R", "drag along the spectrum to mark a region of interest"),
    PlotMode.SELECT: ("Select", "S", "click a peak marker; Ctrl+click adds or removes it"),
}


class SpectrumViewBox(pg.ViewBox):  # type: ignore[misc]  # pyqtgraph has no type stubs
    """ViewBox that hands region drags, clicks and right-clicks to the owning plot."""

    def __init__(self) -> None:
        super().__init__()
        self.owner: SpectrumPlot | None = None
        self.setMenuEnabled(False)  # replaced by the plot's own context menu

    def mouseDragEvent(self, ev: Any, axis: Any = None) -> None:
        owner = self.owner
        if (
            owner is not None
            and owner.mode is PlotMode.REGION
            and ev.button() == Qt.MouseButton.LeftButton
        ):
            ev.accept()
            start = self.mapToView(ev.buttonDownPos()).x()
            now = self.mapToView(ev.pos()).x()
            owner.region_drag(start, now, finished=ev.isFinish())
            return
        super().mouseDragEvent(ev, axis)

    def mouseClickEvent(self, ev: Any) -> None:
        owner = self.owner
        if owner is None:
            super().mouseClickEvent(ev)
            return
        x = self.mapSceneToView(ev.scenePos()).x()
        if ev.button() == Qt.MouseButton.RightButton:
            ev.accept()
            owner.show_context_menu(x, ev.screenPos())
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            if ev.double():
                ev.accept()
                owner.reset_view()
                return
            ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if owner.click_at(x, additive=ctrl):
                ev.accept()
                return
        super().mouseClickEvent(ev)


@dataclass(frozen=True)
class PlotItemSpec:
    spectrum: Spectrum
    label: str
    scale: float = 1.0  # display-only multiplier (ignored in count-rate mode)


class SpectrumPlot(QWidget):
    """Step plot of one or more spectra with cursor, zoom, pan, regions and selection."""

    cursor_moved = Signal(int)  # channel under the cursor
    mode_changed = Signal(str)
    region_drawn = Signal(int, int)  # first, last channel of a newly drawn region
    region_edited = Signal(str, int, int)  # region id, new first, new last
    region_selected = Signal(str)  # region id ("" = none)
    delete_requested = Signal()  # delete the selected region
    fit_requested = Signal()  # fit the selected region
    undo_requested = Signal()
    redo_requested = Signal()
    markers_selected = Signal(list)  # selected marker channels (floats)
    candidate_add_requested = Signal(float)  # add a peak candidate at this channel

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[PlotItemSpec] = []
        self._cursor_channel: int | None = None
        self._marker_lines: list[pg.InfiniteLine] = []
        self._selected_markers: list[float] = []
        self._marker_pens: list[Any] = []
        self._reference_lines: list[pg.InfiniteLine] = []
        self._regions: dict[str, pg.LinearRegionItem] = {}
        self._selected_region = ""
        self._draft: pg.LinearRegionItem | None = None
        self.mode = PlotMode.NAVIGATE

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
        self.view_box = SpectrumViewBox()
        self.view_box.owner = self
        self.plot = pg.PlotWidget(background="w", viewBox=self.view_box)
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
        self.mode_group = QActionGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_actions: dict[PlotMode, QAction] = {}
        self.mode_buttons: dict[PlotMode, QToolButton] = {}
        for mode, (text, key, tip) in MODE_HELP.items():
            action = QAction(text, self)
            action.setCheckable(True)
            action.setToolTip(f"{text} ({key}): {tip}")
            action.triggered.connect(self._mode_slot(mode))
            self.mode_group.addAction(action)
            button = QToolButton()
            button.setDefaultAction(action)
            controls.addWidget(button)
            self.mode_actions[mode] = action
            self.mode_buttons[mode] = button
            self._shortcut(key, self._mode_slot(mode))
        self.mode_actions[PlotMode.NAVIGATE].setChecked(True)
        self._shortcut("Home", self.reset_view)
        self._shortcut("L", self.log_box.toggle)
        self._shortcut(QKeySequence.StandardKey.Delete, self.delete_requested.emit)
        self._shortcut("F", self.fit_requested.emit)
        self._shortcut(QKeySequence.StandardKey.Undo, self.undo_requested.emit)
        self._shortcut(QKeySequence.StandardKey.Redo, self.redo_requested.emit)
        controls.addSpacing(12)
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

    def _shortcut(self, key: Any, slot: Callable[[], Any]) -> QShortcut:
        shortcut = QShortcut(QKeySequence(key), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(slot)
        return shortcut

    def _mode_slot(self, mode: PlotMode) -> Callable[[], None]:
        def slot(*_: object) -> None:
            self.set_mode(mode)

        return slot

    # ------------------------------------------------------------------ public API
    def set_spectra(self, items: list[PlotItemSpec]) -> None:
        self._items = list(items[:MAX_OVERLAY])
        self._redraw()
        self.reset_view()

    def items(self) -> list[PlotItemSpec]:
        return list(self._items)

    def set_markers(
        self,
        channels: Sequence[float],
        styles: Sequence[tuple[str, Qt.PenStyle]] | None = None,
    ) -> None:
        """Mark channel positions (e.g. peak candidates) with thin vertical lines.

        ``styles`` gives one (colour, line style) per marker, e.g. by quality class.
        """
        for line in self._marker_lines:
            self.plot.removeItem(line)
        self._marker_lines = []
        self._selected_markers = []
        self._marker_pens = [
            pg.mkPen(colour, width=1, style=style) for colour, style in styles or ()
        ] or [pg.mkPen(MARKER_PEN, width=1)] * len(channels)
        for ch, pen in zip(channels, self._marker_pens, strict=True):
            line = pg.InfiniteLine(pos=float(ch), angle=90, movable=False, pen=pen)
            line.setZValue(5)
            self.plot.addItem(line, ignoreBounds=True)
            self._marker_lines.append(line)

    def set_reference_lines(self, lines: Sequence[tuple[float, str, float]]) -> None:
        """Draw reference lines (channel, label, relative strength 0..1), dashed blue.

        Positions come from a verified calibration (the caller's responsibility, checked
        in ``nsw.services.nuclear_explorer``); the axis stays in channels.
        """
        for line in self._reference_lines:
            self.plot.removeItem(line)
        self._reference_lines = []
        for channel, label, strength in lines:
            alpha = int(80 + 175 * max(0.0, min(1.0, strength)))
            line = pg.InfiniteLine(
                pos=float(channel),
                angle=90,
                movable=False,
                pen=pg.mkPen((11, 92, 173, alpha), width=1, style=Qt.PenStyle.DashLine),
                label=label,
                labelOpts={"position": 0.92, "color": (11, 92, 173), "rotateAxis": (1, 0)},
            )
            line.setZValue(4)
            self.plot.addItem(line, ignoreBounds=True)
            self._reference_lines.append(line)

    def reference_lines(self) -> list[tuple[float, str]]:
        return [(float(line.value()), str(line.label.format)) for line in self._reference_lines]

    def markers(self) -> list[float]:
        return [float(line.value()) for line in self._marker_lines]

    def selected_markers(self) -> list[float]:
        return list(self._selected_markers)

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

    # ------------------------------------------------------------------ modes
    def set_mode(self, mode: PlotMode) -> None:
        self.mode = PlotMode(mode)
        self.mode_actions[self.mode].setChecked(True)
        self.view_box.setMouseMode(
            pg.ViewBox.RectMode if self.mode is PlotMode.ZOOM_BOX else pg.ViewBox.PanMode
        )
        self.mode_changed.emit(self.mode.value)

    # ------------------------------------------------------------------ regions
    def set_regions(self, regions: Sequence[tuple[str, int, int]], selected: str = "") -> None:
        """Show regions (id, first channel, last channel); edges are drawn at ±0.5."""
        wanted = {rid for rid, _, _ in regions}
        for rid in [r for r in self._regions if r not in wanted]:
            self.plot.removeItem(self._regions.pop(rid))
        for rid, first, last in regions:
            item = self._regions.get(rid)
            if item is None:
                item = pg.LinearRegionItem(orientation="vertical", swapMode="sort")
                item.setZValue(-5)
                item.sigRegionChangeFinished.connect(self._region_slot(rid, item))
                self.plot.addItem(item, ignoreBounds=True)
                self._regions[rid] = item
            item.blockSignals(True)
            item.setRegion((first - 0.5, last + 0.5))
            item.blockSignals(False)
        self._selected_region = selected if selected in self._regions else ""
        for rid, item in self._regions.items():
            brush = ROI_SELECTED_BRUSH if rid == self._selected_region else ROI_BRUSH
            item.setBrush(pg.mkBrush(brush))
            item.update()

    def regions(self) -> dict[str, tuple[int, int]]:
        """Regions as drawn: id -> (first channel, last channel)."""
        out = {}
        for rid, item in self._regions.items():
            lo, hi = item.getRegion()
            out[rid] = (round(lo + 0.5), round(hi - 0.5))
        return out

    def region_item(self, rid: str) -> pg.LinearRegionItem:
        return self._regions[rid]

    def selected_region(self) -> str:
        return self._selected_region

    def region_at(self, x: float) -> str:
        """Id of the most recently drawn region containing x, or ""."""
        for rid, item in reversed(list(self._regions.items())):
            lo, hi = item.getRegion()
            if lo <= x <= hi:
                return str(rid)
        return ""

    def _region_slot(self, rid: str, item: pg.LinearRegionItem) -> Callable[..., None]:
        def slot(*_: object) -> None:
            lo, hi = item.getRegion()
            self.region_edited.emit(rid, round(lo + 0.5), round(hi - 0.5))

        return slot

    def region_drag(self, start: float, now: float, *, finished: bool) -> None:
        """Rubber-band region while dragging; emits ``region_drawn`` on release."""
        lo, hi = sorted((start, now))
        if self._draft is None:
            self._draft = pg.LinearRegionItem(
                values=(lo, hi), movable=False, brush=pg.mkBrush(ROI_BRUSH)
            )
            self.plot.addItem(self._draft, ignoreBounds=True)
        self._draft.setRegion((lo, hi))
        if finished:
            self.plot.removeItem(self._draft)
            self._draft = None
            first, last = round(lo + 0.5), round(hi - 0.5)
            if last > first:
                self.region_drawn.emit(first, last)

    # ------------------------------------------------------------------ clicks and menu
    def click_at(self, x: float, *, additive: bool) -> bool:
        """Handle a left click at channel x; True if it selected something."""
        if self.mode is PlotMode.SELECT and self._marker_lines:
            nearest = min(self.markers(), key=lambda m: abs(m - x))
            if abs(nearest - x) * self._pixels_per_channel() <= MARKER_PICK_PIXELS:
                if additive and nearest in self._selected_markers:
                    self._selected_markers.remove(nearest)
                elif additive:
                    self._selected_markers.append(nearest)
                else:
                    self._selected_markers = [nearest]
                self._paint_markers()
                self.markers_selected.emit(list(self._selected_markers))
                return True
        rid = self.region_at(x)
        if rid != self._selected_region:
            self.region_selected.emit(rid)
        return bool(rid)

    def _pixels_per_channel(self) -> float:
        (x0, x1), _ = self.view_box.viewRange()
        return float(max(self.view_box.width(), 1.0) / max(x1 - x0, 1e-9))

    def context_menu_actions(self, x: float) -> list[QAction]:
        """The actions offered on a right-click at channel x."""
        channel = round(x)
        actions: list[QAction] = []

        def add(text: str, slot: Callable[..., Any], enabled: bool = True) -> None:
            action = QAction(text, self)
            action.setEnabled(enabled)
            action.triggered.connect(slot)
            actions.append(action)

        rid = self.region_at(x)
        has_region = bool(rid or self._selected_region)
        if rid:
            add("Select this region", lambda *_: self.region_selected.emit(rid))
        add(
            f"Add peak candidate at channel {channel}",
            lambda *_: self.candidate_add_requested.emit(float(channel)),
            enabled=bool(self._items),
        )
        add("Fit selected region (F)", lambda *_: self.fit_requested.emit(), enabled=has_region)
        add(
            "Delete selected region (Del)",
            lambda *_: self.delete_requested.emit(),
            enabled=has_region,
        )
        add("Draw regions (R)", self._mode_slot(PlotMode.REGION))
        add("Zoom box (Z)", self._mode_slot(PlotMode.ZOOM_BOX))
        add("Reset view (Home)", lambda *_: self.reset_view())
        return actions

    def show_context_menu(self, x: float, screen_pos: QPointF) -> None:
        menu = QMenu(self)
        for action in self.context_menu_actions(x):
            menu.addAction(action)
        menu.exec(screen_pos.toPoint())

    def _paint_markers(self) -> None:
        for line, base in zip(self._marker_lines, self._marker_pens, strict=True):
            chosen = float(line.value()) in self._selected_markers
            line.setPen(pg.mkPen(MARKER_SELECTED_PEN, width=3) if chosen else base)

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
