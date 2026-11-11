"""Background execution of service calls so the GUI thread never blocks."""

from __future__ import annotations

import gc
import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class WorkerSignals(QObject):
    progress = Signal(int, int, str)  # done, total, message
    result = Signal(object)
    error = Signal(str, str)  # short message, traceback
    finished = Signal()


class Worker(QRunnable):
    """Runs ``fn(*args, progress=..., cancelled=...)`` on a pool thread.

    ``fn`` must accept the keyword arguments ``progress`` and ``cancelled`` if
    ``with_progress`` is true.
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, with_progress: bool = False) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._fn, self._args, self._with_progress = fn, args, with_progress
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # executed on the pool thread
        try:
            if self._with_progress:
                value = self._fn(
                    *self._args,
                    progress=self.signals.progress.emit,
                    cancelled=self._cancel.is_set,
                )
            else:
                value = self._fn(*self._args)
        except Exception as exc:  # report every failure to the GUI
            self.signals.error.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        else:
            self.signals.result.emit(value)
        finally:
            self.signals.finished.emit()


def start(worker: Worker) -> None:
    """Start a worker. Must be called from the GUI thread.

    Pending cyclic garbage is collected here, on the GUI thread, so that Qt objects are
    never destroyed from the worker thread (see ``nsw.ui.app._collect_on_gui_thread``).
    """
    gc.collect()
    QThreadPool.globalInstance().start(worker)
