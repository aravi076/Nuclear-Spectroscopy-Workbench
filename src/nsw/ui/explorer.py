"""Spectrum Explorer page: lists (raw / background / synthetic), plot, metadata panels."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nsw.core.spectrum import MeasurementStatus, Spectrum
from nsw.services import describe
from nsw.ui.plot import MAX_OVERLAY, PlotItemSpec, SpectrumPlot
from nsw.ui.roi_controller import RoiController
from nsw.ui.roi_panel import RoiPanel
from nsw.ui.state import AppState, Entry

COLUMNS = ("Spectrum", "Sample", "Live (s)", "Total counts", "Status", "Start", "Files")
KEY_ROLE = Qt.ItemDataRole.UserRole
TAB_RAW, TAB_BACKGROUND, TAB_SYNTHETIC = 0, 1, 2


class KeyValueTable(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels(["Item", "Value"])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setWordWrap(True)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def set_rows(self, rows: describe.Rows) -> None:
        self.setRowCount(len(rows))
        for r, (key, value) in enumerate(rows):
            self.setItem(r, 0, QTableWidgetItem(key))
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            self.setItem(r, 1, item)
        self.resizeRowsToContents()

    def rows(self) -> describe.Rows:
        return [
            (self.item(r, 0).text(), self.item(r, 1).text())  # type: ignore[union-attr]
            for r in range(self.rowCount())
        ]


class ExplorerPage(QWidget):
    selection_changed = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._entries: dict[str, Entry] = {}

        self.trees: list[QTreeWidget] = []
        self.list_tabs = QTabWidget()
        for title in ("Raw spectra", "Background spectra", "Synthetic spectra"):
            tree = QTreeWidget()
            tree.setColumnCount(len(COLUMNS))
            tree.setHeaderLabels(list(COLUMNS))
            tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            tree.setAlternatingRowColors(True)
            tree.setUniformRowHeights(True)
            tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            tree.customContextMenuRequested.connect(self._context_menu)
            tree.itemSelectionChanged.connect(self._on_selection)
            self.trees.append(tree)
            self.list_tabs.addTab(tree, title)
        self.list_tabs.currentChanged.connect(self._on_selection)

        self.plot = SpectrumPlot()
        self.roi_controller = RoiController(state, self.plot, self)
        self.roi_panel = RoiPanel(self.roi_controller)
        self.measurement_table = KeyValueTable()
        self.detector_table = KeyValueTable()
        self.file_table = KeyValueTable()
        self.history_table = KeyValueTable()
        self.info_tabs = QTabWidget()
        self.info_tabs.addTab(self.measurement_table, "Measurement")
        self.info_tabs.addTab(self.detector_table, "Detector")
        self.info_tabs.addTab(self.file_table, "Original file")
        self.info_tabs.addTab(self.history_table, "Warnings and history")
        # the regions panel scrolls inside its tab so the spectrum plot keeps its height
        roi_scroll = QScrollArea()
        roi_scroll.setWidgetResizable(True)
        roi_scroll.setWidget(self.roi_panel)
        self.info_tabs.addTab(roi_scroll, "Regions of interest")
        self.plot.setMinimumHeight(300)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.plot)
        right.addWidget(self.info_tabs)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.list_tabs)
        split.addWidget(right)
        split.setSizes([560, 940])
        layout = QVBoxLayout(self)
        layout.addWidget(split)

        state.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ population
    def refresh(self) -> None:
        self._entries.clear()
        self._fill(self.trees[TAB_RAW], self.state.experimental_entries(), group_series=True)
        self._fill(self.trees[TAB_BACKGROUND], self.state.background_entries(), prefix="bg:")
        self._fill(self.trees[TAB_SYNTHETIC], self.state.synthetic_entries(), prefix="syn:")
        for i, tree in enumerate(self.trees):
            base = ("Raw spectra", "Background spectra", "Synthetic spectra")[i]
            self.list_tabs.setTabText(i, f"{base} ({self._count(tree)})")
        self._on_selection()

    @staticmethod
    def _count(tree: QTreeWidget) -> int:
        n = 0
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            n += 1 + (top.childCount() if top is not None else 0)
        return n

    def _fill(
        self, tree: QTreeWidget, entries: list[Entry], prefix: str = "", group_series: bool = False
    ) -> None:
        tree.blockSignals(True)
        tree.clear()
        parent_of: dict[str, QTreeWidgetItem] = {}
        # final saves first so intermediate saves can be attached beneath them
        ordered = sorted(
            entries,
            key=lambda e: e.spectrum.measurement_status is MeasurementStatus.INTERMEDIATE_SAVE,
        )
        for entry in ordered:
            s = entry.spectrum
            key = prefix + entry.key
            self._entries[key] = entry
            status = s.measurement_status.value
            if entry.series_size > 1 and s.measurement_status is MeasurementStatus.INDEPENDENT:
                status = f"independent (final of {entry.series_size} saves)"
            if self.state.is_background(entry.key) and not prefix:
                status += "; designated background"
            start = s.start_time_local.strftime("%Y-%m-%d %H:%M:%S") if s.start_time_local else ""
            item = QTreeWidgetItem(
                [
                    entry.title,
                    s.sample_name or "",
                    f"{s.live_time_s:g}" if s.live_time_s else "",
                    f"{s.total:,.0f}",
                    status,
                    start,
                    ", ".join(entry.files),
                ]
            )
            item.setData(0, KEY_ROLE, key)
            for col in (2, 3):
                item.setTextAlignment(
                    col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            series_key = start if group_series and entry.series_size > 1 else ""
            if s.measurement_status is MeasurementStatus.INTERMEDIATE_SAVE:
                for col in range(len(COLUMNS)):
                    item.setForeground(col, QBrush(QColor("#777777")))
                parent = parent_of.get(series_key) if series_key else None
                if parent is not None:
                    parent.addChild(item)
                    continue
            tree.addTopLevelItem(item)
            if series_key and s.measurement_status is MeasurementStatus.INDEPENDENT:
                parent_of[series_key] = item
        tree.sortItems(5, Qt.SortOrder.AscendingOrder)
        for col in range(len(COLUMNS) - 1):
            tree.resizeColumnToContents(col)
        tree.blockSignals(False)

    # ------------------------------------------------------------------ selection
    def current_tree(self) -> QTreeWidget:
        return self.trees[self.list_tabs.currentIndex()]

    def selected_entries(self) -> list[Entry]:
        out: list[Entry] = []
        for item in self.current_tree().selectedItems():
            entry = self._entries.get(item.data(0, KEY_ROLE))
            if entry is not None:
                out.append(entry)
        return out[:MAX_OVERLAY]

    def current_spectrum(self) -> Spectrum | None:
        entries = self.selected_entries()
        return entries[0].spectrum if entries else None

    def select_keys(self, keys: list[str], tab: int = TAB_RAW) -> None:
        """Select entries by key (data hash, with ``bg:``/``syn:`` prefix for those tabs)."""
        self.list_tabs.setCurrentIndex(tab)
        tree = self.trees[tab]
        tree.clearSelection()
        wanted = set(keys)

        def visit(item: QTreeWidgetItem) -> None:
            if item.data(0, KEY_ROLE) in wanted:
                parent = item.parent()
                if parent is not None:
                    parent.setExpanded(True)
                item.setSelected(True)
            for i in range(item.childCount()):
                child = item.child(i)
                if child is not None:
                    visit(child)

        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            if top is not None:
                visit(top)

    def _on_selection(self) -> None:
        entries = self.selected_entries()
        self.plot.set_spectra([PlotItemSpec(e.spectrum, e.title) for e in entries])
        # regions belong to the first (or only) spectrum shown
        self.roi_controller.set_spectrum(entries[0].spectrum if entries else None)
        self.roi_panel.spectrum_opened()
        self.roi_panel.refresh()
        if entries:
            first = entries[0]
            s = first.spectrum
            others = tuple(f for f in first.files if s.source is None or f != s.source.filename)
            self.measurement_table.set_rows(describe.measurement_rows(s))
            self.detector_table.set_rows(describe.detector_rows(s))
            self.file_table.set_rows(describe.file_rows(s, others))
            self.history_table.set_rows(describe.history_rows(s))
        else:
            for table in (
                self.measurement_table,
                self.detector_table,
                self.file_table,
                self.history_table,
            ):
                table.set_rows([])
        self.selection_changed.emit()

    # ------------------------------------------------------------------ context menu
    def _context_menu(self, pos: object) -> None:
        entries = self.selected_entries()
        if len(entries) != 1:
            return
        entry = entries[0]
        tab = self.list_tabs.currentIndex()
        menu = QMenu(self)
        if tab == TAB_RAW and not self.state.is_background(entry.key):
            action = QAction("Designate as background measurement", menu)
            action.setEnabled(entry.spectrum.measurement_status is MeasurementStatus.INDEPENDENT)
            action.triggered.connect(lambda: self.state.designate_background(entry.spectrum))
            menu.addAction(action)
        elif not entry.spectrum.is_synthetic and self.state.is_background(entry.key):
            action = QAction("Remove background designation", menu)
            action.triggered.connect(lambda: self.state.remove_background(entry.key))
            menu.addAction(action)
        if menu.actions():
            menu.exec(self.current_tree().viewport().mapToGlobal(pos))  # type: ignore[call-overload]
