"""Application entry point.

.venv\\Scripts\\python.exe -m nsw.ui.app            # start the application
.venv\\Scripts\\python.exe -m nsw.ui.app --load     # also load the configured datasets
"""

from __future__ import annotations

import argparse
import gc
import sys
from pathlib import Path

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


MIN_GUI_PYTHON = (3, 12)
GUI_PYTHON_MESSAGE = (
    "The NSW desktop interface needs Python 3.12 or later. On Python 3.11 a C extension "
    "of the interface stack (PySide6 6.12) releases Python's None object too often, "
    "which aborts the interpreter after a while ('none_dealloc'); Python 3.12 is immune "
    "(None is immortal there). The calculation engine (nsw.analysis, nsw.services) "
    "works on 3.11. The Windows installer bundles Python 3.12."
)


def check_gui_python(version: tuple[int, ...] = tuple(sys.version_info[:2])) -> None:
    """Refuse to start the interface on an interpreter where it would crash later."""
    if version < MIN_GUI_PYTHON:
        raise SystemExit(GUI_PYTHON_MESSAGE)


def create_application(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName("Nuclear Spectroscopy Workbench")
    _collect_on_gui_thread(app)
    return app


def _preload_in_background() -> None:
    """Import scipy.signal (peak search, ~1.7 s) in a background thread after the window
    is shown, so start-up stays fast and the first automatic search does not stall the
    interface (TD-053, TD-062). Import only: no Qt object is touched from the thread."""
    import threading

    def load() -> None:
        import scipy.signal  # noqa: F401

    threading.Thread(target=load, name="nsw-preload", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nsw")
    parser.add_argument(
        "--load", action="store_true", help="load the datasets configured in config/local.yaml"
    )
    parser.add_argument(
        "--self-test",
        metavar="FOLDER",
        type=Path,
        help="run the end-to-end self-test on synthetic data, writing only into FOLDER",
    )
    args = parser.parse_args(argv)

    check_gui_python()
    app = create_application()
    if args.self_test is not None:
        from nsw.ui import selftest

        return selftest.run(app, args.self_test)
    settings: Settings | None
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        settings = None
        print(f"warning: {exc}", file=sys.stderr)
    window = MainWindow(settings)
    window.show()
    _preload_in_background()
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
