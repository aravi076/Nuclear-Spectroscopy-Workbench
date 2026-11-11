"""Application entry point.

.venv\\Scripts\\python.exe -m nsw.ui.app            # start the application
.venv\\Scripts\\python.exe -m nsw.ui.app --load     # also load the configured datasets
"""

from __future__ import annotations

import argparse
import gc
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from nsw.config import ConfigurationError, Settings, load_settings, project_root
from nsw.ui.main_window import MainWindow

GC_INTERVAL_MS = 1000


def _collect_on_gui_thread(app: QApplication) -> None:
    """Run Python's cyclic garbage collector only on the GUI thread.

    Automatic collection can start in whichever thread happens to allocate. If that is a
    worker thread and the garbage contains Qt objects (widgets, plot items), they are
    destroyed outside the thread they live in, which can deadlock the application
    (observed: a worker frozen inside an XML parse while the GUI thread waited).
    Automatic collection is therefore switched off and a timer collects on the GUI thread.
    """
    if app.property("nsw_gc_timer") is not None:
        return
    gc.disable()
    timer = QTimer(app)
    timer.setInterval(GC_INTERVAL_MS)
    timer.timeout.connect(_collect)
    timer.start()
    app.setProperty("nsw_gc_timer", timer)


def _collect() -> None:
    gc.collect()


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName("Nuclear Spectroscopy Workbench")
    _collect_on_gui_thread(app)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nsw")
    parser.add_argument(
        "--load", action="store_true", help="load the datasets configured in config/local.yaml"
    )
    args = parser.parse_args(argv)

    app = create_application()
    settings: Settings | None
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        settings = None
        print(f"warning: {exc}", file=sys.stderr)
    window = MainWindow(settings)
    window.show()
    if args.load:
        if settings is not None:
            for root in settings.paths.experimental_roots:
                if root.is_dir():
                    window.load_experimental(root)
                    break
        synthetic = project_root() / "data" / "synthetic"
        if synthetic.is_dir():
            window.load_synthetic(synthetic)
    return int(app.exec())


if __name__ == "__main__":
    sys.exit(main())
