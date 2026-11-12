"""Builders that fabricate small files in the verified formats, for parser tests.

These are *format* fixtures (fabricated bytes with known content), not measurements.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from datetime import datetime

NS = "http://www.canberra.com/mcaanywhair"
DEFAULT_START = "Tue Mar 10 09:15:27 GMT+0530 2026"
DEFAULT_START_MS = 1773114327892  # the same instant, with milliseconds


def lynx_csv(
    counts: Sequence[int | float | str],
    *,
    start: str = DEFAULT_START,
    live: float | str = 100,
    real: float | str = 101.5,
    cal: tuple[float, float, float] = (-1.5, 0.25, 0.0),
    newline: str = "\r\n",
) -> bytes:
    lines = [
        f"Start Time, {start}",
        f"Energy calibration,  Offset: {cal[0]}, Slope: {cal[1]}, Quadratic: {cal[2]}",
        f"Live Time (s), {live}",
        f"Real Time (s), {real}",
        "Elapsed Computational, 0",
        "Spectrum",
        "Channel, Energy (keV), Counts",
    ]
    # the energy column is deliberately nonsense: the adapter must ignore it
    lines += [f"{i + 1}, {999.0 + i}, {c}" for i, c in enumerate(counts)]
    return (newline.join(lines) + newline).encode("utf-8")


def lynx_ava(
    counts: Sequence[int | str],
    *,
    start: str | None = DEFAULT_START,
    start_ms: int | None = DEFAULT_START_MS,
    live: float | str = 100,
    real: float | str = 101.5,
    cal: tuple[float, ...] = (-1.5, 0.25, 0.0),
    sample: str = "sample-A",
    serial: str = "12345",
    gain: int | None = None,
    extra: str = "",
    prolog: str = "",
) -> bytes:
    attrs = f'elapsed_live="{live}" elapsed_real="{real}" elapsed_deadtime="1.0"'
    if start is not None:
        attrs += f' start="{start}"'
    if start_ms is not None:
        attrs += f' start_time_ms="{start_ms}"'
    coeffs = "".join(f'<coefficient value="{c}" />' for c in cal)
    gain = len(counts) if gain is None else gain
    return f"""<?xml version="1.0" encoding="UTF-8"?>{prolog}
<mcadata xmlns="{NS}">
<parameter code="39" value="1800" name="Preset Live Time (s)" input="1"/>
<parameter code="61" value="2048" name="Status" input="1"/>
<parameter code="61" value="0" name="Status" input="1"/>
<parameter code="70" value="{gain}" name="Conversion Gain" input="1"/>
<parameter code="80" value="{serial}" name="Serial Number" input="1"/>
<parameter code="81" value="Lynx-II" name="Detector Name" input="1"/>
<parameter code="0" value="HPGe" name="Detector Name" input="1"/>
<parameter code="2" value="{sample}" name="Sample Name" input="1"/>
<parameter code="3" value="a description" name="Sample Description" input="1"/>
<spectrum {attrs}>{", ".join(str(c) for c in counts)}</spectrum>
<calibration_details type="energy">
<model type="polynomial" chi_square="0" is_default="true">{coeffs}<covariance></covariance></model>
</calibration_details>
{extra}
</mcadata>
""".encode()


def _pdp11(value: float) -> bytes:
    raw = struct.pack("<f", value * 4.0)
    return raw[2:4] + raw[0:2]


def canberra_cnf(
    counts: Sequence[int],
    *,
    start: datetime = datetime(2026, 3, 10, 9, 15, 27),
    live: float = 100.0,
    real: float = 102.0,
    cal: tuple[float, float, float] = (-1.5, 0.25, 0.0),
    sample: str = "sample-A",
    include_data_section: bool = True,
) -> bytes:
    """A minimal CAM file with the fields the adapter decodes. len(counts) % 256 == 0."""
    n = len(counts)
    assert n % 256 == 0 and 0 < n <= 255 * 256
    acq, smp, dat = 0x800, 0x1000, 0x1800
    buf = bytearray(dat + 0x200 + 4 * n)

    def entry(index: int, sid: int, loc: int) -> None:
        off = 0x70 + 0x30 * index
        struct.pack_into("<I", buf, off, sid)
        struct.pack_into("<I", buf, off + 0x0A, loc)

    entry(0, 0x00012000, acq)
    entry(1, 0x00012001, smp)
    if include_data_section:
        entry(2, 0x00012005, dat)
    rec_off, tim_off = 0x100, 0x300
    struct.pack_into("<H", buf, acq + 0x22, rec_off)
    struct.pack_into("<H", buf, acq + 0x24, tim_off)
    buf[acq + 0xBA] = n // 256
    rec, tim = acq + 0x30 + rec_off, acq + 0x30 + tim_off
    for i, c in enumerate(cal):
        buf[rec + 0x44 + 4 * i : rec + 0x48 + 4 * i] = _pdp11(c)
    ticks = int((start - datetime(1858, 11, 17)).total_seconds() * 10_000_000)
    struct.pack_into("<Q", buf, tim + 0x01, ticks)
    struct.pack_into("<q", buf, tim + 0x09, -round(real * 10_000_000))
    struct.pack_into("<q", buf, tim + 0x11, -round(live * 10_000_000))
    name = sample.encode("latin-1")
    buf[smp + 0x30 : smp + 0x30 + len(name)] = name
    struct.pack_into(f"<{n}I", buf, dat + 0x200, *counts)
    return bytes(buf)
