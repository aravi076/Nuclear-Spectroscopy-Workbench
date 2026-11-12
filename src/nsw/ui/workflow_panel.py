"""Guided-workflow panel and the beginner / advanced mode switch.

The panel lists the twelve stages (``nsw.services.workflow``) for the spectrum selected
in the Spectrum Explorer, with their status and the reason when blocked, and highlights
the suggested next step. Clicking a stage opens its page. The mode changes only what is
visible: in Beginner mode the panel is shown and widgets marked as advanced are hidden;
calculations and state are the same in both modes (R10), so switching loses nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nsw.services import workflow as wf

ADVANCED_PROPERTY = "nswAdvanced"
BEGINNER, ADVANCED = "beginner", "advanced"
STATUS_COLOURS = {
    wf.StageStatus.DONE: "#2ca02c",
    wf.StageStatus.READY: "#0b5cad",
    wf.StageStatus.BLOCKED: "#b00020",
    wf.StageStatus.OPTIONAL: "#7f7f7f",
}
STATUS_MARKS = {
    wf.StageStatus.DONE: "✓",
    wf.StageStatus.READY: "→",
    wf.StageStatus.BLOCKED: "✗",
    wf.StageStatus.OPTIONAL: "○",
}


def mark_advanced(*widgets: QWidget) -> None:
    """Hide these widgets in Beginner mode."""
    for w in widgets:
        w.setProperty(ADVANCED_PROPERTY, True)


class WorkflowPanel(QWidget):
    mode_changed = Signal(str)
    open_page = Signal(str)  # page title

    def __init__(
        self, context: Callable[[], wf.WorkflowContext], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._context = context
        self.mode_box = QComboBox()
        self.mode_box.addItem("Beginner", BEGINNER)
        self.mode_box.addItem("Advanced", ADVANCED)
        self.mode_box.setToolTip(
            "Beginner: guided steps, fewer controls. Advanced: every control. The analysis "
            "is identical in both."
        )
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setMinimumWidth(150)
        self.next_label = QLabel()
        self.next_label.setWordWrap(True)
        self.next_label.setStyleSheet("font-weight: bold;")
        row = QHBoxLayout()
        row.addWidget(QLabel("Mode:"))
        row.addWidget(self.mode_box, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        title = QLabel("Workflow for the selected spectrum")
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.next_label)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        self.list.itemActivated.connect(self._activated)
        self.list.itemClicked.connect(self._activated)
        self.refresh()

    def mode(self) -> str:
        return str(self.mode_box.currentData())

    def set_mode(self, mode: str) -> None:
        self.mode_box.setCurrentIndex(self.mode_box.findData(mode))

    def refresh(self) -> None:
        stages = wf.stages(self._context())
        self.list.clear()
        for st in stages:
            item = QListWidgetItem(
                f"{STATUS_MARKS[st.status]} {st.number}. {st.name} — {st.detail}"
            )
            item.setData(Qt.ItemDataRole.UserRole, st.page)
            item.setForeground(QBrush(QColor(STATUS_COLOURS[st.status])))
            item.setToolTip(f"{st.status.value}: {st.detail}. Click to open {st.page}.")
            self.list.addItem(item)
        nxt = wf.next_step(stages)
        self.next_label.setText(
            f"Suggested next step: {nxt.name} ({nxt.page})" if nxt else "No step is ready."
        )

    def stage_texts(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def _activated(self, item: QListWidgetItem) -> None:
        self.open_page.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def _mode_changed(self) -> None:
        self.mode_changed.emit(self.mode())


def apply_mode(root: QWidget, mode: str) -> int:
    """Show or hide every widget marked advanced below ``root``; returns how many."""
    n = 0
    for w in root.findChildren(QWidget):
        if w.property(ADVANCED_PROPERTY):
            w.setVisible(mode == ADVANCED)
            n += 1
    return n
