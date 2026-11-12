"""Measure how long the application takes to import and to show its main window.

Each run is a fresh interpreter (no module cache in memory). The first run after a
reboot or a rebuild is "cold" (files not in the operating-system cache); later runs are
"warm". The script reports every run so cold and warm can be told apart.

    .venv\\Scripts\\python.exe scripts\\measure_startup.py --runs 5
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time

IMPORT_ONLY = "import nsw.ui.main_window"
SHOW_WINDOW = (
    "from PySide6.QtWidgets import QApplication\n"
    "app = QApplication([])\n"
    "from nsw.ui.main_window import MainWindow\n"
    "w = MainWindow()\n"
    "w.show()\n"
    "app.processEvents()\n"
)


def _time(code: str) -> float:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    start = time.perf_counter()
    subprocess.run([sys.executable, "-c", code], check=True, env=env)  # noqa: S603 - own interpreter, fixed code
    return time.perf_counter() - start


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    for name, code in (("import", IMPORT_ONLY), ("main window shown", SHOW_WINDOW)):
        times = [_time(code) for _ in range(args.runs)]
        listed = ", ".join(f"{t:.2f}" for t in times)
        print(
            f"{name}: runs [{listed}] s; median of runs 2..n "
            f"{statistics.median(times[1:] or times):.2f} s"
        )


if __name__ == "__main__":
    main()
