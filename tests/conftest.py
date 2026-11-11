"""Shared test helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from nsw.core import DataOrigin, SourceFile, Spectrum, SpectrumKind

FAKE_SHA = "0" * 64


def make_source(name: str = "file.csv") -> SourceFile:
    return SourceFile(
        path=f"X:/data/{name}",
        filename=name,
        size_bytes=10,
        sha256=FAKE_SHA,
        format="test",
        adapter="TestAdapter",
    )


def make_raw(counts: Any = (0, 1, 5, 20, 5, 1, 0, 0), **kw: Any) -> Spectrum:
    defaults: dict[str, Any] = dict(
        kind=SpectrumKind.RAW,
        origin=DataOrigin.EXPERIMENTAL,
        live_time_s=100.0,
        real_time_s=101.0,
        source=make_source(),
    )
    defaults.update(kw)
    return Spectrum(counts=np.asarray(counts), **defaults)


def _experimental_root() -> Path | None:
    try:
        from nsw.config import load_settings

        for root in load_settings().paths.experimental_roots:
            if root.is_dir() and any(root.iterdir()):
                return root
    except Exception:
        return None
    return None


@pytest.fixture(scope="session")
def experimental_root() -> Path:
    """The read-only laboratory dataset; tests using it are skipped when unavailable."""
    root = _experimental_root()
    if root is None:
        pytest.skip("experimental dataset not configured on this machine")
    return root
