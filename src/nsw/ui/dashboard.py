"""Project Dashboard page: what was loaded, what was found, integrity status."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nsw.ui.state import AppState


class DashboardPage(QWidget):
    open_experimental_requested = Signal()
    open_synthetic_requested = Signal()
    verify_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state

        self.open_button = QPushButton("Open experimental dataset folder…")
        self.synthetic_button = QPushButton("Open synthetic dataset folder…")
        self.verify_button = QPushButton("Verify dataset integrity (SHA-256 manifest)")
        self.verify_button.setEnabled(False)
        buttons = QHBoxLayout()
        for b in (self.open_button, self.synthetic_button, self.verify_button):
            buttons.addWidget(b)
        buttons.addStretch(1)

        self.root_label = QLabel("No experimental dataset loaded.")
        self.root_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.synthetic_label = QLabel("No synthetic dataset loaded.")
        self.integrity_label = QLabel("Integrity: not checked in this session.")
        self.summary_form = QFormLayout()
        self._summary_labels: dict[str, QLabel] = {}
        summary_box = QGroupBox("Experimental dataset")
        box_layout = QVBoxLayout(summary_box)
        box_layout.addWidget(self.root_label)
        box_layout.addWidget(self.integrity_label)
        box_layout.addLayout(self.summary_form)
        box_layout.addWidget(self.synthetic_label)

        self.findings = QTableWidget(0, 2)
        self.findings.setHorizontalHeaderLabels(["Finding", "Details"])
        self.findings.verticalHeader().setVisible(False)
        self.findings.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.findings.setWordWrap(True)
        header = self.findings.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        findings_box = QGroupBox("Data-identity findings and import problems")
        QVBoxLayout(findings_box).addWidget(self.findings)

        note = QLabel(
            "Files are identified by content, never by name. Successive saves of one "
            "acquisition are not independent measurements. Experimental data are opened "
            "read-only."
        )
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(summary_box)
        layout.addWidget(findings_box, 1)
        layout.addWidget(note)

        self.open_button.clicked.connect(self.open_experimental_requested)
        self.synthetic_button.clicked.connect(self.open_synthetic_requested)
        self.verify_button.clicked.connect(self.verify_requested)
        state.changed.connect(self.refresh)
        self.refresh()

    def set_integrity_text(self, text: str, ok: bool | None) -> None:
        colour = {True: "#1b7f3b", False: "#b00020", None: "#555555"}[ok]
        self.integrity_label.setText(text)
        self.integrity_label.setStyleSheet(f"color: {colour}; font-weight: bold;")

    def summary_value(self, key: str) -> str:
        return self._summary_labels[key].text()

    def refresh(self) -> None:
        scan = self.state.experimental
        while self.summary_form.rowCount():
            self.summary_form.removeRow(0)
        self._summary_labels.clear()
        rows: list[tuple[str, str]] = []
        if scan is None:
            self.root_label.setText("No experimental dataset loaded.")
            self.verify_button.setEnabled(False)
        else:
            self.root_label.setText(f"Folder (read-only): {scan.root}")
            self.verify_button.setEnabled(True)
            for key, value in scan.summary().items():
                label = QLabel(str(value))
                self._summary_labels[key] = label
                self.summary_form.addRow(key.capitalize() + ":", label)
            for failure in scan.failures:
                kind = "unsupported file" if failure.unsupported else "MALFORMED FILE"
                rows.append((kind, f"{Path(failure.path).name}: {failure.reason}"))
            for finding in scan.identity.findings:
                rows.append((finding.kind.value, finding.message))
        syn = self.state.synthetic
        self.synthetic_label.setText(
            "No synthetic dataset loaded."
            if syn is None
            else f"Synthetic dataset: {syn.root} ({len(syn.spectra)} spectra)"
        )
        self.findings.setRowCount(len(rows))
        for r, (kind, message) in enumerate(rows):
            self.findings.setItem(r, 0, QTableWidgetItem(kind))
            self.findings.setItem(r, 1, QTableWidgetItem(message))
        self.findings.resizeRowsToContents()
