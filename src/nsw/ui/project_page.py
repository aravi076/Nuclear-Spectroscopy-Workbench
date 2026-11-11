"""Project page: create, open, save and back up an NSW project file; analysis history."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nsw.services import project as project_service
from nsw.ui.tables import DataTable


class ProjectPage(QWidget):
    new_requested = Signal()
    open_requested = Signal()
    save_requested = Signal()
    backup_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.new_button = QPushButton("New project…")
        self.open_button = QPushButton("Open project…")
        self.save_button = QPushButton("Save session to project")
        self.backup_button = QPushButton("Back up project")
        buttons = QHBoxLayout()
        for b in (self.new_button, self.open_button, self.save_button, self.backup_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.new_button.clicked.connect(self.new_requested)
        self.open_button.clicked.connect(self.open_requested)
        self.save_button.clicked.connect(self.save_requested)
        self.backup_button.clicked.connect(self.backup_requested)

        self.info_label = QLabel("No project open. Results of this session are not saved.")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        info_box = QGroupBox("Project")
        info_layout = QVBoxLayout(info_box)
        info_layout.addWidget(self.info_label)
        info_layout.addWidget(self.status_label)

        self.history = DataTable()
        history_box = QGroupBox("Analysis history (append-only: nothing is overwritten)")
        QVBoxLayout(history_box).addWidget(self.history)
        self.problems = QPlainTextEdit()
        self.problems.setReadOnly(True)
        self.problems.setMaximumHeight(90)
        self.problems.setPlaceholderText("Problems found when the project was reopened")

        note = QLabel(
            "A project file stores the spectra used, background designations, calibrations "
            "and every analysis result with its settings and software version. Peak fits are "
            "fitted again when a project is opened and must reproduce the stored values. "
            "Project files are never written into an experimental dataset folder."
        )
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(info_box)
        layout.addWidget(history_box, 1)
        layout.addWidget(self.problems)
        layout.addWidget(note)
        self.set_project(None)

    def set_project(
        self,
        info: dict[str, object] | None,
        runs: list[project_service.StoredRun] | None = None,
        names: dict[str, str] | None = None,
    ) -> None:
        open_ = info is not None
        self.save_button.setEnabled(open_)
        self.backup_button.setEnabled(open_)
        if info is None:
            self.info_label.setText("No project open. Results of this session are not saved.")
            self.history.set_table([], [])
            return
        self.info_label.setText(
            f"<b>{info['name']}</b> — {info['path']}<br>created {info['created']} by nsw "
            f"{info['created_by_software']}; schema {info['schema_version']}"
        )
        headers, rows = project_service.history_table(runs or [], names or {})
        self.history.set_table(headers, rows)

    def set_status(self, text: str, ok: bool = True) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("" if ok else "color: #b00020;")

    def set_problems(self, problems: list[str]) -> None:
        self.problems.setPlainText("\n".join(problems))
