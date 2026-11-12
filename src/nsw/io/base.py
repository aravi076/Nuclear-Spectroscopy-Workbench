"""Common pieces of the import system: errors, hashing, the reader protocol.

Import never writes: files are opened in binary read mode only.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from nsw.core.errors import NSWError
from nsw.core.spectrum import SourceFile, Spectrum


class SpectrumImportError(NSWError):
    """Base class of import failures; always names the file and the reason."""

    def __init__(self, path: Path | str, reason: str) -> None:
        self.path = Path(path)
        self.reason = reason
        super().__init__(f"{self.path.name}: {reason}")


class UnsupportedFormatError(SpectrumImportError):
    """The file content matches no registered spectrum format."""


class MalformedFileError(SpectrumImportError):
    """The file was recognised as a known format but its content is invalid."""


class SpectrumReader(Protocol):
    """A file-format adapter. One class per verified format."""

    format_name: str

    def sniff(self, head: bytes) -> bool:
        """True if the first bytes of a file identify this format (content, not extension)."""
        ...

    def read(self, path: Path, data: bytes) -> Spectrum:
        """Parse the complete file content. Raises :class:`MalformedFileError`."""
        ...


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_source(path: Path, data: bytes, format_name: str, adapter: object) -> SourceFile:
    return SourceFile(
        path=str(path.resolve()),
        filename=path.name,
        size_bytes=len(data),
        sha256=sha256_bytes(data),
        format=format_name,
        adapter=type(adapter).__name__,
    )


_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}
_LYNX_TIME = re.compile(
    r"^\w{3} (\w{3}) +(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) GMT([+-])(\d{2})(\d{2}) (\d{4})$"
)


def parse_lynx_timestamp(text: str) -> datetime:
    """Parse e.g. ``Tue Mar 10 09:15:27 GMT+0530 2026`` into a timezone-aware datetime.

    Parsed by hand so that the result does not depend on the process locale.
    """
    m = _LYNX_TIME.match(text.strip())
    if not m or m.group(1) not in _MONTHS:
        raise ValueError(f"unrecognised timestamp {text!r}")
    mon, day, hh, mm, ss, sign, oh, om, year = m.groups()
    offset = timedelta(hours=int(oh), minutes=int(om))
    if sign == "-":
        offset = -offset
    try:
        return datetime(
            int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss), tzinfo=timezone(offset)
        )
    except ValueError as exc:
        raise ValueError(f"invalid timestamp {text!r}: {exc}") from exc


def is_whole_seconds(*times: float | None) -> bool:
    """True if every given time is an exact whole number of seconds."""
    present = [t for t in times if t is not None]
    return bool(present) and all(float(t).is_integer() for t in present)
