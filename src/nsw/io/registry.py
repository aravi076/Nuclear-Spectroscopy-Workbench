"""Format detection by file content and the single entry point :func:`read_spectrum`.

Only formats verified against real files (plus NSW's own synthetic format) are
registered. Anything else is rejected with :class:`UnsupportedFormatError`; no format is
guessed from the file extension.
"""

from __future__ import annotations

from pathlib import Path

from nsw.core.spectrum import Spectrum
from nsw.io.base import MalformedFileError, SpectrumReader, UnsupportedFormatError
from nsw.io.canberra_cnf import CanberraCnfAdapter
from nsw.io.lynx_ava import LynxAvaAdapter
from nsw.io.lynx_csv import LynxCsvAdapter
from nsw.io.nsw_synthetic import NswSyntheticAdapter

SNIFF_BYTES = 4096
MAX_FILE_BYTES = 64 * 1024 * 1024  # spectra are small; refuse to slurp huge files

_READERS: list[SpectrumReader] = [
    LynxAvaAdapter(),
    LynxCsvAdapter(),
    CanberraCnfAdapter(),
    NswSyntheticAdapter(),
]

_KNOWN_NON_SPECTRA = {
    b"%PDF": "PDF document",
    b"PK\x03\x04": "ZIP archive / Office document",
    b"\x89PNG": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
}


def registered_formats() -> tuple[str, ...]:
    return tuple(r.format_name for r in _READERS)


def register_reader(reader: SpectrumReader) -> None:
    """Add a format adapter (extension point; existing code is not modified)."""
    _READERS.append(reader)


def detect_reader(path: Path, head: bytes) -> SpectrumReader:
    if not head:
        raise MalformedFileError(path, "file is empty")
    matches = [r for r in _READERS if r.sniff(head)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(r.format_name for r in matches)
        raise UnsupportedFormatError(path, f"ambiguous content, matches several formats: {names}")
    for magic, what in _KNOWN_NON_SPECTRA.items():
        if head.startswith(magic):
            raise UnsupportedFormatError(path, f"{what}, not a spectrum file")
    raise UnsupportedFormatError(
        path,
        "content matches no supported spectrum format (supported: "
        + "; ".join(registered_formats())
        + ")",
    )


def read_spectrum(path: Path | str) -> Spectrum:
    """Read one spectrum file. The file is opened read-only and never modified."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise UnsupportedFormatError(path, f"file is {size} bytes; too large for a spectrum")
        with path.open("rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise MalformedFileError(path, f"cannot read file: {exc}") from exc
    reader = detect_reader(path, data[:SNIFF_BYTES])
    return reader.read(path, data)
