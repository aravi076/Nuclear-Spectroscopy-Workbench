"""Synthetic input data for the application self-test (fictitious sources only)."""

from __future__ import annotations

from pathlib import Path

from nsw.simulation.generator import generate, write_synthetic
from nsw.simulation.sources import source_scenarios


def write_source_spectra(folder: Path) -> Path:
    """Write the SYN-CAL and SYN-X spectra into ``folder`` (created if needed)."""
    folder.mkdir(parents=True, exist_ok=True)
    for spec in source_scenarios():
        target = folder / f"{spec.synthetic_id}.nswsyn.json"
        if not target.exists():
            write_synthetic(generate(spec), folder)
    return folder
