"""NSW file import: content-sniffed, read-only adapters for verified formats."""

from nsw.io.base import (
    MalformedFileError,
    SpectrumImportError,
    SpectrumReader,
    UnsupportedFormatError,
)
from nsw.io.identity import (
    Acquisition,
    Finding,
    FindingKind,
    IdentityReport,
    Snapshot,
    analyse_identity,
)
from nsw.io.registry import read_spectrum, register_reader, registered_formats

__all__ = [
    "Acquisition",
    "Finding",
    "FindingKind",
    "IdentityReport",
    "MalformedFileError",
    "Snapshot",
    "SpectrumImportError",
    "SpectrumReader",
    "UnsupportedFormatError",
    "analyse_identity",
    "read_spectrum",
    "register_reader",
    "registered_formats",
]
