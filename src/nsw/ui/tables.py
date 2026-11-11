"""Read-only result table shared by the analysis pages."""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem


class DataTable(QTableWidget):
    """A read-only table filled from (headers, rows of strings)."""

    def __init__(self) -> None:
        super().__init__(0, 0)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def set_table(self, headers: list[str], rows: list[list[str]]) -> None:
        self.clear()
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                self.setItem(r, c, QTableWidgetItem(text))

    def headers(self) -> list[str]:
        return [
            self.horizontalHeaderItem(c).text()  # type: ignore[union-attr]
            for c in range(self.columnCount())
        ]

    def rows(self) -> list[list[str]]:
        return [
            [self.item(r, c).text() for c in range(self.columnCount())]  # type: ignore[union-attr]
            for r in range(self.rowCount())
        ]

    def column_values(self, header: str) -> list[str]:
        c = self.headers().index(header)
        return [row[c] for row in self.rows()]
