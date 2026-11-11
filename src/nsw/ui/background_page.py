"""Background Subtraction page. All calculations are delegated to ``nsw.services``."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nsw.core.spectrum import Spectrum
from nsw.services import background as background_service
from nsw.ui.explorer import KeyValueTable
from nsw.ui.plot import PlotItemSpec, SpectrumPlot
from nsw.ui.state import AppState, Entry

NO_BACKGROUND_HINT = (
    "No background measurement has been designated. In the Spectrum Explorer, right-click "
    "an independent measurement and choose “Designate as background measurement”. "
    "NSW never decides from a file name that a spectrum is a background."
)


class BackgroundPage(QWidget):
    export_requested = Signal()

    def __init__(self, state: AppState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.result: background_service.BackgroundSubtraction | None = None
        self._gross: list[Entry] = []
        self._backgrounds: list[Entry] = []

        self.gross_box = QComboBox()
        self.background_box = QComboBox()
        for box in (self.gross_box, self.background_box):
            box.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            box.setMinimumContentsLength(40)
        self.subtract_button = QPushButton("Check and subtract")
        self.export_button = QPushButton("Export net spectrum as CSV…")
        self.export_button.setEnabled(False)
        form = QFormLayout()
        form.addRow("Gross spectrum:", self.gross_box)
        form.addRow("Background (designated):", self.background_box)
        buttons = QHBoxLayout()
        buttons.addWidget(self.subtract_button)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)

        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMaximumHeight(130)
        self.status = QLabel()
        self.status.setWordWrap(True)

        self.plot = SpectrumPlot()
        self.result_table = KeyValueTable()
        self.comparison_table = KeyValueTable()
        self.comparison_table.setHorizontalHeaderLabels(["Background pair", "Rate comparison"])
        tabs = QTabWidget()
        tabs.addTab(self.result_table, "Result")
        tabs.addTab(self.comparison_table, "Comparison of designated backgrounds")

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addLayout(form)
        top_layout.addLayout(buttons)
        top_layout.addWidget(self.status)
        top_layout.addWidget(self.messages)
        top_layout.addWidget(self.plot, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(tabs)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        QVBoxLayout(self).addWidget(split)

        self.subtract_button.clicked.connect(self.run)
        self.export_button.clicked.connect(self.export_requested)
        self.gross_box.currentIndexChanged.connect(self._inputs_changed)
        self.background_box.currentIndexChanged.connect(self._inputs_changed)
        state.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------ population
    @staticmethod
    def _describe(entry: Entry) -> str:
        s = entry.spectrum
        tag = "SYNTHETIC  " if s.is_synthetic else ""
        live = f"{s.live_time_s:g} s" if s.live_time_s else "no live time"
        return f"{tag}{entry.title}   [{live}, {s.measurement_status.value}]"

    def refresh(self) -> None:
        bg_keys = {e.key for e in self.state.background_entries()}
        self._gross = [
            e
            for e in (*self.state.experimental_entries(), *self.state.synthetic_entries())
            if e.key not in bg_keys
        ]
        self._backgrounds = self.state.background_entries()
        for box, entries in (
            (self.gross_box, self._gross),
            (self.background_box, self._backgrounds),
        ):
            current = box.currentData()
            box.blockSignals(True)
            box.clear()
            for e in entries:
                box.addItem(self._describe(e), e.key)
            index = box.findData(current)
            box.setCurrentIndex(index if index >= 0 else (0 if entries else -1))
            box.blockSignals(False)
        self._refresh_comparison()
        self._inputs_changed()

    def _refresh_comparison(self) -> None:
        experimental = [e for e in self._backgrounds if not e.spectrum.is_synthetic]
        rows: list[tuple[str, str]] = []
        for i, a in enumerate(experimental):
            for b in experimental[i + 1 :]:
                rows.append(
                    (
                        f"{a.title}  vs  {b.title}",
                        background_service.comparison_text(a.spectrum, b.spectrum),
                    )
                )
        if not rows:
            rows.append(("—", "Designate at least two independent backgrounds to compare them."))
        self.comparison_table.set_rows(rows)

    # ------------------------------------------------------------------ selection
    def select(self, gross_key: str, background_key: str) -> None:
        self.gross_box.setCurrentIndex(self.gross_box.findData(gross_key))
        self.background_box.setCurrentIndex(self.background_box.findData(background_key))

    def _selected(self) -> tuple[Spectrum | None, Spectrum | None]:
        gi, bi = self.gross_box.currentIndex(), self.background_box.currentIndex()
        gross = self._gross[gi].spectrum if 0 <= gi < len(self._gross) else None
        bg = self._backgrounds[bi].spectrum if 0 <= bi < len(self._backgrounds) else None
        return gross, bg

    def _set_status(self, text: str, colour: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-weight: bold;")

    def _inputs_changed(self) -> None:
        """Selections changed: discard the previous result and show the validity check."""
        self.result = None
        self.export_button.setEnabled(False)
        self.result_table.set_rows([])
        gross, bg = self._selected()
        if bg is None:
            self.subtract_button.setEnabled(False)
            self._set_status("Background subtraction not available.", "#555555")
            self.messages.setPlainText(NO_BACKGROUND_HINT)
            self.plot.set_spectra([])
            return
        if gross is None:
            self.subtract_button.setEnabled(False)
            self._set_status("No gross spectrum available.", "#555555")
            self.messages.setPlainText("")
            self.plot.set_spectra([])
            return
        compat = background_service.check(gross, bg)
        lines = [f"REFUSED: {e}" for e in compat.errors] + [
            f"Caution: {w}" for w in compat.warnings
        ]
        self.messages.setPlainText("\n".join(lines) if lines else "No objections from metadata.")
        self.subtract_button.setEnabled(compat.ok)
        if compat.ok:
            self._set_status("Combination is valid. Press “Check and subtract”.", "#1b7f3b")
        else:
            self._set_status("This combination is not valid; calculation disabled.", "#b00020")
        self.plot.set_spectra([PlotItemSpec(gross, "gross"), PlotItemSpec(bg, "background")])

    # ------------------------------------------------------------------ calculation
    def run(self) -> None:
        gross, bg = self._selected()
        if gross is None or bg is None:
            return
        try:
            result = background_service.subtract(gross, bg)
        except background_service.ProcessingError as exc:
            self.result = None
            self._set_status("Calculation refused.", "#b00020")
            self.messages.setPlainText(str(exc))
            return
        self.result = result
        self.export_button.setEnabled(True)
        self._set_status("Net spectrum calculated (original spectra unchanged).", "#1b7f3b")
        self.result_table.set_rows(background_service.result_rows(result))
        self.plot.set_spectra(
            [
                PlotItemSpec(result.gross, "gross"),
                PlotItemSpec(
                    result.background, f"background × α ({result.alpha:.4g})", result.alpha
                ),
                PlotItemSpec(result.net, "net = gross − α·background"),
            ]
        )

    def net_spectrum(self) -> Spectrum | None:
        return self.result.net if self.result is not None else None

    def suggested_filename(self) -> str:
        if self.result is None:
            return "net.csv"
        g = self.result.gross
        stem = Path(g.source.filename).stem if g.source else (g.synthetic_id or "spectrum")
        return f"{stem}_net.csv"
