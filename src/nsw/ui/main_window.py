"""Main application window: navigation, pages, menus, background workers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QInputDialog,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QWidget,
)

import nsw
from nsw.config import Settings, project_root
from nsw.core.spectrum import Spectrum
from nsw.services import identification as identification_service
from nsw.services import project as project_service
from nsw.services import report as report_service
from nsw.services.calibration import export_calibration_csv
from nsw.services.dataset import DatasetScan, scan_dataset
from nsw.services.export import ExportError, ensure_output_allowed, export_spectrum_csv
from nsw.services.integrity import IntegrityReport, verify_dataset
from nsw.services.peaks import export_peaks_csv
from nsw.services.resolution import export_resolution_csv
from nsw.services.workflow import WorkflowContext
from nsw.ui import workers
from nsw.ui.background_page import BackgroundPage
from nsw.ui.calibration_page import CalibrationPage
from nsw.ui.dashboard import DashboardPage
from nsw.ui.explorer import ExplorerPage
from nsw.ui.identification_page import IdentificationPage
from nsw.ui.nuclear_data_page import NuclearDataPage
from nsw.ui.peak_fit_page import PeakFitPage
from nsw.ui.peak_search_page import PeakSearchPage
from nsw.ui.project_page import ProjectPage
from nsw.ui.quantification_page import ActivityPage, EfficiencyPage
from nsw.ui.resolution_page import ResolutionPage
from nsw.ui.state import AppState
from nsw.ui.workflow_panel import BEGINNER, WorkflowPanel, apply_mode, mark_advanced

PAGE_DASHBOARD, PAGE_EXPLORER, PAGE_BACKGROUND, PAGE_PEAK_SEARCH, PAGE_PEAK_FIT = 0, 1, 2, 3, 4
PAGE_CALIBRATION, PAGE_RESOLUTION, PAGE_EFFICIENCY, PAGE_ACTIVITY = 5, 6, 7, 8
PAGE_NUCLEAR_DATA, PAGE_IDENTIFICATION, PAGE_PROJECT = 9, 10, 11


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
        if settings is not None:
            self.explorer.roi_panel.set_method(settings.analysis.detection_limits.method)
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
        self.efficiency_page = EfficiencyPage(self.state)
        self._add_page("Efficiency Calibration", self.efficiency_page)
        self.activity_page = ActivityPage(self.state)
        self._add_page("Activity Calculation", self.activity_page)
        self.nuclear_data_page = NuclearDataPage(self.state)
        self._add_page("Nuclear Data Explorer", self.nuclear_data_page)
        self.identification_page = IdentificationPage(self.state)
        self._add_page("Nuclide Identification", self.identification_page)
        self.project: project_service.ProjectStore | None = None
        self.project_page = ProjectPage()
        self._add_page("Project File", self.project_page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(PAGE_DASHBOARD)

        self._integrity_ok = False
        self.workflow = WorkflowPanel(self.workflow_context)
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(self.nav)
        left.addWidget(self.workflow)
        left.setStretchFactor(1, 1)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
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
        self.workflow.open_page.connect(self.open_page)
        self.workflow.mode_changed.connect(self.set_mode)
        self.explorer.selection_changed.connect(self.workflow.refresh)
        for signal in (
            self.state.changed,
            self.state.peaks_changed,
            self.state.fits_changed,
            self.state.calibrations_changed,
            self.state.efficiency_changed,
            self.state.libraries_changed,
        ):
            signal.connect(self.workflow.refresh)
        mark_advanced(
            self.explorer.roi_panel.edge_box,
            self.explorer.roi_panel.compare_button,
            self.explorer.roi_panel.tail_button,
            self.explorer.roi_panel.threshold_box,
            self.explorer.roi_panel.threshold_label,
            self.explorer.roi_panel.edge_label,
            self.calibration_page.inflate_check,
            self.calibration_page.order_button,
            self.calibration_page.assist_unc,
        )
        self.set_mode(BEGINNER)
        self.background_page.export_requested.connect(self.export_net_dialog)
        self.peak_fit_page.export_requested.connect(self.export_peaks_dialog)
        self.calibration_page.export_requested.connect(self.export_calibration_dialog)
        self.resolution_page.export_requested.connect(self.export_resolution_dialog)
        self.identification_page.load_library_requested.connect(self.load_library_dialog)
        self.nuclear_data_page.load_library_requested.connect(self.load_library_dialog)
        self.identification_page.export_requested.connect(self.export_identification_dialog)
        self.project_page.new_requested.connect(self.new_project_dialog)
        self.project_page.open_requested.connect(self.open_project_dialog)
        self.project_page.save_requested.connect(self.save_session)
        self.project_page.backup_requested.connect(self.backup_project)
        self._update_actions()

    # ------------------------------------------------------------------ construction
    def _add_page(self, title: str, widget: QWidget) -> None:
        self.nav.addItem(title)
        # a page larger than a small screen scrolls instead of widening the window
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        self.pages.addWidget(scroll)

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
        self.act_report = QAction("Export analysis report (PDF)…", self)
        self.act_report.triggered.connect(self.export_report_dialog)
        self.act_figure = QAction("Export spectrum figure (SVG, PDF or PNG)…", self)
        self.act_figure.triggered.connect(self.export_figure_dialog)
        self.act_png = QAction("Export plot as PNG…", self)
        self.act_png.triggered.connect(self.export_png_dialog)
        self.act_new_project = QAction("New project…", self)
        self.act_new_project.triggered.connect(self.new_project_dialog)
        self.act_open_project = QAction("Open project…", self)
        self.act_open_project.triggered.connect(self.open_project_dialog)
        self.act_save_project = QAction("Save session to project", self)
        self.act_save_project.setShortcut("Ctrl+S")
        self.act_save_project.triggered.connect(self.save_session)
        act_quit = QAction("Exit", self)
        act_quit.triggered.connect(self.close)
        for a in (self.act_open, self.act_open_syn):
            file_menu.addAction(a)
        file_menu.addSeparator()
        for a in (self.act_new_project, self.act_open_project, self.act_save_project):
            file_menu.addAction(a)
        file_menu.addSeparator()
        for a in (self.act_csv, self.act_png, self.act_figure, self.act_report):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

    # ------------------------------------------------------------------ workflow and modes
    def workflow_context(self) -> WorkflowContext:
        s = self.explorer.current_spectrum() if hasattr(self, "explorer") else None
        key = s.data_sha256 if s is not None else ""
        cal_verified = bool(
            s is not None and s.energy_calibration is not None and s.energy_calibration.is_verified
        )
        provisional = any(item.data_sha256 == key for item in self.state.calibration_history)
        efficiency = self.state.efficiency is not None
        resolution_page = getattr(self, "resolution_page", None)
        return WorkflowContext(
            spectra_loaded=bool(self.state.all_entries()),
            spectrum_selected=s is not None,
            synthetic=bool(s is not None and s.is_synthetic),
            integrity_checked=self._integrity_ok if hasattr(self, "_integrity_ok") else False,
            detector_known=bool(s is not None and s.detector_id),
            background_designated=bool(self.state.background_entries()),
            peak_search_done=bool(key and self.state.peak_search(key) is not None),
            fits_done=bool(key and self.state.fits(key)),
            calibration_verified=cal_verified,
            calibration_provisional=provisional and not cal_verified,
            resolution_done=bool(resolution_page is not None and resolution_page.fit is not None),
            efficiency_done=efficiency,
            activity_possible=bool(efficiency and s is not None and s.is_synthetic),
            library_loaded=bool(self.state.libraries),
            project_open=getattr(self, "project", None) is not None,
        )

    def open_page(self, title: str) -> None:
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            if item is not None and item.text() == title:
                self.nav.setCurrentRow(i)
                return

    def set_mode(self, mode: str) -> None:
        """Beginner: workflow panel shown, advanced controls hidden. Same analysis."""
        self.mode = mode
        if self.workflow.mode() != mode:
            self.workflow.set_mode(mode)
        self.workflow.setVisible(True)
        apply_mode(self, mode)
        self.workflow.refresh()

    def _update_actions(self) -> None:
        has = self.explorer.current_spectrum() is not None
        self.act_save_project.setEnabled(self.project is not None)
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
        self._integrity_ok = report.ok
        self.workflow.refresh()
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
        import pyqtgraph.exporters  # imported on use: 1.4 s at start-up otherwise (R1)

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

    # ------------------------------------------------------------------ reports
    def report_spectrum(self) -> Spectrum | None:
        """The spectrum of the page in front, else the one selected in the explorer."""
        holder = self.pages.currentWidget()
        page = holder.widget() if isinstance(holder, QScrollArea) else holder
        current = getattr(page, "current_spectrum", None)
        spectrum = current() if callable(current) else None
        return spectrum if spectrum is not None else self.explorer.current_spectrum()

    def build_report(self, spectrum: Spectrum) -> report_service.Report:
        key = spectrum.data_sha256
        efficiency = self.state.efficiency
        activity = self.activity_page.result
        ident = self.identification_page
        same_activity = activity is not None and self.activity_page.current_spectrum() is spectrum
        same_ident = ident.result is not None and ident.current_spectrum() is spectrum
        return report_service.build_report(
            spectrum,
            search=self.state.peak_search(key),
            fits=self.state.fits(key),
            efficiency=efficiency[1] if efficiency is not None and efficiency[0] == key else None,
            activity=activity if same_activity else None,
            identification=ident.result if same_ident else None,
            library=ident.current_library() if same_ident else None,
        )

    def export_report(self, path: Path, overwrite: bool = False) -> Path | None:
        spectrum = self.report_spectrum()
        if spectrum is None:
            self._error("No spectrum", "Select a spectrum first.")
            return None
        try:
            out = report_service.export_report(
                self.build_report(spectrum), path, self.protected_roots(), overwrite
            )
        except ExportError as exc:
            self._error("Report not written", str(exc))
            return None
        self.statusBar().showMessage(f"Report written: {out}", 10000)
        return out

    def export_report_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Export analysis report", self._default_output_dir(), "PDF (*.pdf)"
        )
        if name:
            self.export_report(Path(name), overwrite=True)

    def export_figure(self, path: Path, overwrite: bool = False) -> Path | None:
        spectrum = self.report_spectrum()
        if spectrum is None:
            self._error("No spectrum", "Select a spectrum first.")
            return None
        try:
            out = report_service.export_figure(
                spectrum, self.state.fits(spectrum.data_sha256), path, self.protected_roots(),
                overwrite,
            )  # fmt: skip
        except ExportError as exc:
            self._error("Figure not written", str(exc))
            return None
        self.statusBar().showMessage(f"Figure written: {out}", 10000)
        return out

    def export_figure_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export spectrum figure",
            self._default_output_dir(),
            "SVG (*.svg);;PDF (*.pdf);;PNG (*.png)",
        )
        if name:
            self.export_figure(Path(name), overwrite=True)

    # ------------------------------------------------------------------ nuclide page
    def load_library_dialog(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Load NSW reference-data file", self._default_output_dir(), "JSON (*.json)"
        )
        if name:
            self.load_library(Path(name))

    def load_library(self, path: Path) -> bool:
        try:
            library = identification_service.load_reference_file(path)
        except (identification_service.ProcessingError, OSError) as exc:
            self._error("Reference data not loaded", str(exc))
            return False
        self.identification_page.add_library(library)
        self.state.add_library(library)
        if self.project is not None:
            project_service.save_reference_library(self.project, library)
            self._show_project()
        return True

    def export_identification(self, path: Path, overwrite: bool = False) -> Path | None:
        page = self.identification_page
        s = page.current_spectrum()
        if s is None or page.result is None:
            return None
        try:
            return identification_service.export_identification_csv(
                s, page.result, path, self.protected_roots(), overwrite
            )
        except ExportError as exc:
            self._error("Export failed", str(exc))
            return None

    def export_identification_dialog(self) -> None:
        start = str(
            Path(self._default_output_dir()) / self.identification_page.suggested_filename()
        )
        name, _ = QFileDialog.getSaveFileName(
            self, "Export nuclide candidates", start, "CSV (*.csv)"
        )
        if name:
            self.export_identification(Path(name), overwrite=True)

    # ------------------------------------------------------------------ project file
    def _project_dir(self) -> str:
        if self.settings is not None:
            workspace = self.settings.paths.workspace
            if workspace.is_dir():
                return str(workspace)
        return self._default_output_dir()

    def _show_project(self) -> None:
        if self.project is None:
            self.project_page.set_project(None)
        else:
            names = {sha: name for sha, name, _, _ in self.project.spectrum_names()}
            self.project_page.set_project(self.project.info, self.project.runs(), names)
        self._update_actions()

    def _set_project(self, store: project_service.ProjectStore | None) -> None:
        if self.project is not None:
            self.project.close()
        self.project = store
        self._show_project()

    def new_project_dialog(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "New NSW project", self._project_dir(), "NSW project (*.nswproj)"
        )
        if not name:
            return
        title, ok = QInputDialog.getText(self, "Project name", "Name of the project:")
        if ok and title.strip():
            self.new_project(Path(name), title)

    def new_project(self, path: Path, title: str) -> bool:
        try:
            store = project_service.create_project(path, title, self.protected_roots())
        except (project_service.ProjectStoreError, OSError) as exc:
            self._error("Project not created", str(exc))
            return False
        self._set_project(store)
        self.project_page.set_status("New project created. Save the session to store results.")
        return True

    def open_project_dialog(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Open NSW project", self._project_dir(), "NSW project (*.nswproj)"
        )
        if name:
            self.open_project(Path(name))

    def open_project(self, path: Path) -> bool:
        """Open a project and replace the session with its contents."""
        try:
            store = project_service.open_project(path)
            contents = project_service.load_project(store)
        except (project_service.ProjectStoreError, OSError, ValueError) as exc:
            self._error("Project not opened", str(exc))
            return False
        experimental, synthetic = project_service.session_scans(contents, store.path)
        self.state.set_experimental(experimental)
        self.state.set_synthetic(synthetic)
        problems = list(contents.problems)
        for sha, note in contents.backgrounds.items():
            entry = self.state.entry(sha)
            if entry is None or entry.spectrum.is_synthetic:
                continue
            try:
                self.state.designate_background(entry.spectrum, note)
            except ValueError as exc:
                problems.append(f"background designation not restored: {exc}")
        for sha, fits in contents.fits.items():
            for fit in fits:
                self.state.store_fit(sha, fit)
        for item in contents.calibration_items:
            self.state.add_calibration(item)
        for rois in contents.rois.values():
            for roi in rois:
                self.state.put_roi(roi)
        for sha, channels in contents.excluded.items():
            self.state.set_excluded_candidates(sha, channels)
        for sha, decisions in contents.assignments.items():
            for decision in decisions:
                self.state.record_assignment(sha, decision)
        self._set_project(store)
        self.project_page.set_problems(problems)
        n_fits = sum(len(f) for f in contents.fits.values())
        self.project_page.set_status(
            f"Opened: {len(contents.spectra)} spectra, {n_fits} fits reproduced, "
            f"{len(contents.calibration_items)} calibrations reproduced, "
            f"{contents.digests_verified} result digests verified, "
            f"{sum(len(r) for r in contents.rois.values())} regions restored"
            + (f"; {len(problems)} problems (listed below)" if problems else "."),
            ok=not problems,
        )
        return True

    def save_session(self) -> project_service.SaveReport | None:
        if self.project is None:
            self._error("No project open", "Create or open a project first.")
            return None
        items = []
        for entry in self.state.all_entries():
            search = self.state.peak_search(entry.key)
            fits = tuple(self.state.fits(entry.key))
            rois = tuple(self.state.rois(entry.key))
            excluded = self.state.excluded_candidates(entry.key)
            assignments = tuple(self.state.assignments(entry.key))
            if search is not None or fits or rois or excluded or assignments:
                items.append(
                    project_service.SessionSpectrum(
                        entry.spectrum, search, fits, rois, excluded, assignments
                    )
                )
        calibrations = []
        for item in self.state.calibration_history:
            owner = self.state.entry(item.data_sha256) if item.data_sha256 else None
            summary = {"chi2": item.fit.chi2, "ndf": item.fit.ndf, "p_value": item.fit.p_value}
            calibrations.append(
                project_service.SessionCalibration(
                    owner.spectrum if owner else None, item.record, summary
                )
            )
        backgrounds = [e.spectrum for e in self.state.background_entries()]
        try:
            report = project_service.save_session(self.project, items, calibrations, backgrounds)
        except (project_service.ProjectStoreError, ValueError, OSError) as exc:
            self._error("Session not saved", str(exc))
            return None
        self._show_project()
        self.project_page.set_status(report.text(), ok=not report.skipped)
        self.statusBar().showMessage("Session saved to the project.", 10000)
        return report

    def backup_project(self) -> Path | None:
        if self.project is None:
            return None
        try:
            target = self.project.backup()
        except (project_service.ProjectStoreError, OSError) as exc:
            self._error("Backup failed", str(exc))
            return None
        self.project_page.set_status(f"Backup written: {target}")
        self._show_project()
        return target

    def closeEvent(self, event: QCloseEvent) -> None:
        for worker in list(self._active_workers):
            worker.cancel()
        if self.project is not None:
            self.project.close()
            self.project = None
        super().closeEvent(event)
