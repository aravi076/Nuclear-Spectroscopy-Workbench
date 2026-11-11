"""Main application window: navigation, pages, menus, background workers."""

from __future__ import annotations

from pathlib import Path

import pyqtgraph.exporters
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QWidget,
)

import nsw
from nsw.config import Settings, project_root
from nsw.core.spectrum import Spectrum
from nsw.services.calibration import export_calibration_csv
from nsw.services.dataset import DatasetScan, scan_dataset
from nsw.services.export import ExportError, ensure_output_allowed, export_spectrum_csv
from nsw.services.integrity import IntegrityReport, verify_dataset
from nsw.services.peaks import export_peaks_csv
from nsw.services.resolution import export_resolution_csv
from nsw.ui import workers
from nsw.ui.background_page import BackgroundPage
from nsw.ui.calibration_page import CalibrationPage
from nsw.ui.dashboard import DashboardPage
from nsw.ui.explorer import ExplorerPage
from nsw.ui.peak_fit_page import PeakFitPage
from nsw.ui.peak_search_page import PeakSearchPage
from nsw.ui.resolution_page import ResolutionPage
from nsw.ui.state import AppState

PAGE_DASHBOARD, PAGE_EXPLORER, PAGE_BACKGROUND, PAGE_PEAK_SEARCH, PAGE_PEAK_FIT = 0, 1, 2, 3, 4
PAGE_CALIBRATION, PAGE_RESOLUTION = 5, 6


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.state = AppState()
        self._active_workers: list[workers.Worker] = []
        self.setWindowTitle(f"Nuclear Spectroscopy Workbench {nsw.__version__}")
        self.resize(1500, 900)

        self.nav = QListWidget()
        self.nav.setMaximumWidth(220)
        self.pages = QStackedWidget()
        self.dashboard = DashboardPage(self.state)
        self.explorer = ExplorerPage(self.state)
        self._add_page("Project Dashboard", self.dashboard)
        self._add_page("Spectrum Explorer", self.explorer)
        self.background_page = BackgroundPage(self.state)
        self._add_page("Background Subtraction", self.background_page)
        self.peak_search_page = PeakSearchPage(self.state, self._run_for_page)
        self._add_page("Peak Analysis", self.peak_search_page)
        self.peak_fit_page = PeakFitPage(self.state, self._run_for_page)
        self._add_page("Peak Fitting", self.peak_fit_page)
        self.calibration_page = CalibrationPage(self.state)
        self._add_page("Energy Calibration", self.calibration_page)
        self.resolution_page = ResolutionPage(self.state)
        self._add_page("Resolution Analysis", self.resolution_page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(PAGE_DASHBOARD)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.nav)
        split.addWidget(self.pages)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Ready.")

        self._build_menu()
        self.dashboard.open_experimental_requested.connect(self.choose_experimental_folder)
        self.dashboard.open_synthetic_requested.connect(self.choose_synthetic_folder)
        self.dashboard.verify_requested.connect(self.verify_integrity)
        self.explorer.selection_changed.connect(self._update_actions)
        self.background_page.export_requested.connect(self.export_net_dialog)
        self.peak_fit_page.export_requested.connect(self.export_peaks_dialog)
        self.calibration_page.export_requested.connect(self.export_calibration_dialog)
        self.resolution_page.export_requested.connect(self.export_resolution_dialog)
        self._update_actions()

    # ------------------------------------------------------------------ construction
    def _add_page(self, title: str, widget: QWidget) -> None:
        self.nav.addItem(title)
        self.pages.addWidget(widget)

    def add_page(self, title: str, widget: QWidget) -> int:
        """Extension point for later phases; returns the page index."""
        self._add_page(title, widget)
        return self.pages.count() - 1

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.act_open = QAction("Open experimental dataset folder…", self)
        self.act_open.setShortcut("Ctrl+O")
        self.act_open.triggered.connect(self.choose_experimental_folder)
        self.act_open_syn = QAction("Open synthetic dataset folder…", self)
        self.act_open_syn.triggered.connect(self.choose_synthetic_folder)
        self.act_csv = QAction("Export selected spectrum as CSV…", self)
        self.act_csv.triggered.connect(self.export_csv_dialog)
        self.act_png = QAction("Export plot as PNG…", self)
        self.act_png.triggered.connect(self.export_png_dialog)
        act_quit = QAction("Exit", self)
        act_quit.triggered.connect(self.close)
        for a in (self.act_open, self.act_open_syn):
            file_menu.addAction(a)
        file_menu.addSeparator()
        for a in (self.act_csv, self.act_png):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

    def _update_actions(self) -> None:
        has = self.explorer.current_spectrum() is not None
        self.act_csv.setEnabled(has)
        self.act_png.setEnabled(has)

    # ------------------------------------------------------------------ helpers
    def protected_roots(self) -> list[Path]:
        roots = list(self.settings.paths.experimental_roots) if self.settings else []
        if self.state.experimental is not None:
            roots.append(self.state.experimental.root)
        return roots

    def _default_output_dir(self) -> str:
        out = project_root() / "reports"
        return str(out if out.is_dir() else Path.home())

    def _error(self, title: str, message: str) -> None:
        self.statusBar().showMessage(f"{title}: {message}", 15000)
        QMessageBox.warning(self, title, message)

    def _run_for_page(self, worker: workers.Worker, message: str) -> None:
        """Run a page's worker; the page reports the outcome itself (no dialog)."""
        worker.signals.result.connect(self._page_worker_succeeded)
        self._run(worker, message, show_errors=False)

    def _page_worker_succeeded(self, _result: object) -> None:
        self.statusBar().showMessage("Ready.")

    def _run(self, worker: workers.Worker, message: str, show_errors: bool = True) -> None:
        self._active_workers.append(worker)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.statusBar().showMessage(message)
        worker.signals.progress.connect(self._on_progress)
        if show_errors:
            worker.signals.error.connect(lambda msg, tb: self._error("Operation failed", msg))
        else:
            worker.signals.error.connect(lambda msg, tb: self.statusBar().showMessage(msg, 15000))
        worker.signals.finished.connect(lambda: self._on_finished(worker))
        workers.start(worker)

    def _on_progress(self, done: int, total: int, text: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.statusBar().showMessage(f"{done}/{total}  {text}")

    def _on_finished(self, worker: workers.Worker) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        if not self._active_workers:
            self.progress.hide()

    def is_busy(self) -> bool:
        return bool(self._active_workers)

    # ------------------------------------------------------------------ dataset loading
    def choose_experimental_folder(self) -> None:
        start = ""
        if self.settings and self.settings.paths.experimental_roots:
            start = str(self.settings.paths.experimental_roots[0])
        folder = QFileDialog.getExistingDirectory(self, "Open experimental dataset folder", start)
        if folder:
            self.load_experimental(Path(folder))

    def choose_synthetic_folder(self) -> None:
        start = str(project_root() / "data" / "synthetic")
        folder = QFileDialog.getExistingDirectory(self, "Open synthetic dataset folder", start)
        if folder:
            self.load_synthetic(Path(folder))

    def load_experimental(self, folder: Path) -> None:
        worker = workers.Worker(scan_dataset, folder, with_progress=True)
        worker.signals.result.connect(self._experimental_loaded)
        self._run(worker, f"Reading {folder} (read-only)…")

    def load_synthetic(self, folder: Path) -> None:
        worker = workers.Worker(scan_dataset, folder, with_progress=True)
        worker.signals.result.connect(self._synthetic_loaded)
        self._run(worker, f"Reading synthetic spectra in {folder}…")

    def _experimental_loaded(self, scan: DatasetScan) -> None:
        mixed = [s for s in scan.spectra if s.is_synthetic]
        if mixed:
            self._error(
                "Dataset not loaded",
                f"{len(mixed)} synthetic spectra found in a folder opened as experimental "
                "data. Synthetic and experimental data must be kept in separate folders.",
            )
            return
        self.state.set_experimental(scan)
        self.dashboard.set_integrity_text("Integrity: not checked in this session.", None)
        summary = scan.summary()
        self.statusBar().showMessage(
            f"Loaded {summary['spectrum files imported']} spectrum files: "
            f"{summary['independent measurements']} independent measurements, "
            f"{summary['intermediate saves']} intermediate saves, "
            f"{summary['findings']} findings.",
            20000,
        )

    def _synthetic_loaded(self, scan: DatasetScan) -> None:
        real = [s for s in scan.spectra if not s.is_synthetic]
        if real:
            self._error(
                "Dataset not loaded",
                f"{len(real)} non-synthetic spectra found in a folder opened as synthetic data.",
            )
            return
        self.state.set_synthetic(scan)
        self.statusBar().showMessage(f"Loaded {len(scan.spectra)} synthetic spectra.", 10000)

    # ------------------------------------------------------------------ integrity
    def manifest_path(self) -> Path:
        base = self.settings.paths.manifests if self.settings else Path("local")
        if not base.is_absolute():
            base = project_root() / base
        return base / "dataset_manifest_sha256.csv"

    def verify_integrity(self) -> None:
        scan = self.state.experimental
        if scan is None:
            return
        manifest = self.manifest_path()
        if not manifest.exists():
            self.dashboard.set_integrity_text(f"Integrity: no manifest found at {manifest}", None)
            return
        worker = workers.Worker(verify_dataset, scan.root, manifest)
        worker.signals.result.connect(self._integrity_done)
        self._run(worker, "Verifying SHA-256 manifest…")

    def _integrity_done(self, report: IntegrityReport) -> None:
        if report.ok:
            self.dashboard.set_integrity_text(
                f"Integrity: OK — all {report.scanned} files match the recorded SHA-256 manifest.",
                True,
            )
        else:
            self.dashboard.set_integrity_text(
                f"Integrity: MISMATCH — {len(report.changed)} changed, {len(report.added)} added, "
                f"{len(report.removed)} removed relative to the manifest.",
                False,
            )
        self.statusBar().showMessage("Integrity check finished.", 8000)

    # ------------------------------------------------------------------ export
    def export_csv(
        self, path: Path, overwrite: bool = False, spectrum: Spectrum | None = None
    ) -> Path | None:
        spectrum = spectrum if spectrum is not None else self.explorer.current_spectrum()
        if spectrum is None:
            return None
        try:
            out = export_spectrum_csv(spectrum, path, self.protected_roots(), overwrite)
        except ExportError as exc:
            self._error("Export refused", str(exc))
            return None
        self.statusBar().showMessage(f"Exported {out}", 10000)
        return out

    def export_png(self, path: Path, overwrite: bool = False) -> Path | None:
        try:
            out = ensure_output_allowed(path, self.protected_roots(), overwrite)
        except ExportError as exc:
            self._error("Export refused", str(exc))
            return None
        exporter = pyqtgraph.exporters.ImageExporter(self.explorer.plot.plot.getPlotItem())
        exporter.parameters()["width"] = 1600
        exporter.export(str(out))
        if not out.exists():
            self._error("Export failed", f"could not write {out}")
            return None
        self.statusBar().showMessage(f"Exported {out}", 10000)
        return out

    def export_csv_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Export spectrum as CSV", self._default_output_dir(), "CSV (*.csv)"
        )
        if name:  # the dialog has already asked for overwrite confirmation
            self.export_csv(Path(name), overwrite=True)

    def export_net_dialog(self) -> None:
        net = self.background_page.net_spectrum()
        if net is None:
            return
        start = str(Path(self._default_output_dir()) / self.background_page.suggested_filename())
        name, _ = QFileDialog.getSaveFileName(
            self, "Export net spectrum as CSV", start, "CSV (*.csv)"
        )
        if name:
            self.export_csv(Path(name), overwrite=True, spectrum=net)

    def export_peaks(self, path: Path, overwrite: bool = False) -> Path | None:
        spectrum = self.peak_fit_page.current_spectrum()
        if spectrum is None:
            return None
        try:
            out = export_peaks_csv(
                spectrum, self.peak_fit_page.results(), path, self.protected_roots(), overwrite
            )
        except ExportError as exc:
            self._error("Export refused", str(exc))
            return None
        self.statusBar().showMessage(f"Exported {out}", 10000)
        return out

    def export_peaks_dialog(self) -> None:
        start = str(Path(self._default_output_dir()) / self.peak_fit_page.suggested_filename())
        name, _ = QFileDialog.getSaveFileName(
            self, "Export fitted peaks as CSV", start, "CSV (*.csv)"
        )
        if name:
            self.export_peaks(Path(name), overwrite=True)

    def export_calibration(self, path: Path, overwrite: bool = False) -> Path | None:
        page = self.calibration_page
        spectrum = page.current_spectrum()
        if spectrum is None or page.fit is None:
            return None
        try:
            out = export_calibration_csv(
                spectrum, page.fit, path, self.protected_roots(), overwrite
            )
        except ExportError as exc:
            self._error("Export refused", str(exc))
            return None
        self.statusBar().showMessage(f"Exported {out}", 10000)
        return out

    def export_resolution(self, path: Path, overwrite: bool = False) -> Path | None:
        page = self.resolution_page
        spectrum = page.current_spectrum()
        if spectrum is None or page.fit is None:
            return None
        try:
            out = export_resolution_csv(spectrum, page.fit, path, self.protected_roots(), overwrite)
        except ExportError as exc:
            self._error("Export refused", str(exc))
            return None
        self.statusBar().showMessage(f"Exported {out}", 10000)
        return out

    def export_resolution_dialog(self) -> None:
        start = str(Path(self._default_output_dir()) / self.resolution_page.suggested_filename())
        name, _ = QFileDialog.getSaveFileName(
            self, "Export resolution analysis as CSV", start, "CSV (*.csv)"
        )
        if name:
            self.export_resolution(Path(name), overwrite=True)

    def export_calibration_dialog(self) -> None:
        start = str(Path(self._default_output_dir()) / self.calibration_page.suggested_filename())
        name, _ = QFileDialog.getSaveFileName(
            self, "Export energy calibration as CSV", start, "CSV (*.csv)"
        )
        if name:
            self.export_calibration(Path(name), overwrite=True)

    def export_png_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Export plot as PNG", self._default_output_dir(), "PNG (*.png)"
        )
        if name:
            self.export_png(Path(name), overwrite=True)

    def closeEvent(self, event: QCloseEvent) -> None:
        for worker in list(self._active_workers):
            worker.cancel()
        super().closeEvent(event)
