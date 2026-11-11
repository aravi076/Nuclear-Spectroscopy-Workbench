"""Use case: scan a folder of spectrum files (read-only) and establish what was measured."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nsw.core.spectrum import Spectrum
from nsw.io.base import SpectrumImportError, UnsupportedFormatError
from nsw.io.identity import IdentityReport, analyse_identity
from nsw.io.registry import read_spectrum

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ImportFailure:
    path: str
    reason: str
    unsupported: bool  # True: not a spectrum format at all; False: recognised but malformed


@dataclass(frozen=True)
class DatasetScan:
    root: Path
    spectra: tuple[Spectrum, ...]  # one per successfully imported file
    failures: tuple[ImportFailure, ...]
    identity: IdentityReport
    cancelled: bool = False

    @property
    def n_files(self) -> int:
        return len(self.spectra) + len(self.failures)

    def summary(self) -> dict[str, int]:
        ident = self.identity
        return {
            "files found": self.n_files,
            "spectrum files imported": len(self.spectra),
            "unsupported files": sum(f.unsupported for f in self.failures),
            "malformed files": sum(not f.unsupported for f in self.failures),
            "distinct spectra (snapshots)": len(ident.snapshots),
            "acquisitions": len(ident.acquisitions),
            "independent measurements": ident.n_independent,
            "intermediate saves": len(ident.snapshots)
            - ident.n_independent
            - sum(1 for a in ident.acquisitions if not a.cumulative_verified for _ in a.snapshots),
            "findings": len(ident.findings),
        }


def scan_dataset(
    root: Path | str,
    progress: ProgressCallback | None = None,
    cancelled: CancelCheck | None = None,
) -> DatasetScan:
    """Import every file below ``root``. Nothing is written anywhere."""
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    paths = sorted(p for p in root.rglob("*") if p.is_file())
    spectra: list[Spectrum] = []
    failures: list[ImportFailure] = []
    was_cancelled = False
    for i, path in enumerate(paths):
        if cancelled is not None and cancelled():
            was_cancelled = True
            break
        if progress is not None:
            progress(i, len(paths), path.name)
        try:
            spectra.append(read_spectrum(path))
        except SpectrumImportError as exc:
            failures.append(
                ImportFailure(str(path), exc.reason, isinstance(exc, UnsupportedFormatError))
            )
    if progress is not None:
        progress(len(paths), len(paths), "analysing measurement identity")
    return DatasetScan(
        root, tuple(spectra), tuple(failures), analyse_identity(spectra), was_cancelled
    )
