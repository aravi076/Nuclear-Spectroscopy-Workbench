"""Analysis reports as PDF, and spectrum figures as SVG, PDF or PNG (Matplotlib).

The reporting layer formats what the service layer gives it; it computes nothing. A
report is a title, a status banner, an optional spectrum figure and a sequence of
sections, each with key/value rows, a table and free text. Long content flows onto
further pages. Matplotlib is used without pyplot (no global state, no GUI backend), so
reports can be written from any thread.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from numpy.typing import NDArray

PAGE = (8.27, 11.69)  # A4 portrait, inches
MARGIN_X = 0.07
TOP = 0.95
BOTTOM = 0.05
LINE = 0.0135  # fraction of the page height per text line at 8 pt
WRAP = 110  # characters per line
TABLE_WIDTH = 132  # monospace characters that fit the text width
BANNER_COLOURS = {"synthetic": "#0b5cad", "provisional": "#b00020", "experimental": "#333333"}


@dataclass(frozen=True)
class Overlay:
    x: NDArray[np.float64]
    y: NDArray[np.float64]
    label: str
    colour: str = "#c00000"


@dataclass(frozen=True)
class SpectrumFigure:
    x: NDArray[np.float64]
    y: NDArray[np.float64]
    x_label: str
    y_label: str = "Counts per channel"
    log_y: bool = True
    overlays: tuple[Overlay, ...] = ()
    markers: tuple[float, ...] = ()
    title: str = ""


@dataclass(frozen=True)
class Section:
    title: str
    rows: Sequence[tuple[str, str]] = ()
    table: tuple[Sequence[str], Sequence[Sequence[str]]] | None = None
    text: str = ""


@dataclass(frozen=True)
class Report:
    title: str
    banner: str
    banner_kind: str  # "synthetic", "provisional" or "experimental"
    sections: Sequence[Section] = ()
    figure: SpectrumFigure | None = None
    footer: str = ""
    extra_figures: Sequence[SpectrumFigure] = field(default_factory=tuple)


def draw_spectrum(
    fig: Figure, spec: SpectrumFigure, rect: tuple[float, float, float, float]
) -> None:
    ax = fig.add_axes(rect)
    y = np.asarray(spec.y, dtype=np.float64)
    if spec.log_y:
        floor = 0.5
        ax.set_yscale("log")
        y = np.where(y > floor, y, floor)
        ax.set_ylabel(f"{spec.y_label} (empty channels at {floor:g})", fontsize=7)
    else:
        ax.set_ylabel(spec.y_label, fontsize=7)
    ax.step(spec.x, y, where="mid", color="#1f1f1f", linewidth=0.6, label="spectrum")
    for o in spec.overlays:
        oy = np.where(o.y > 0.5, o.y, 0.5) if spec.log_y else o.y
        ax.plot(o.x, oy, color=o.colour, linewidth=0.9, label=o.label or "_nolegend_")
    for m in spec.markers:
        ax.axvline(m, color="#e08000", linewidth=0.5, alpha=0.7)
    ax.set_xlabel(spec.x_label, fontsize=7)
    ax.tick_params(labelsize=6)
    ax.grid(alpha=0.2)
    if spec.title:
        ax.set_title(spec.title, fontsize=8)
    if spec.overlays:
        ax.legend(fontsize=6, loc="upper right")


def write_figure(spec: SpectrumFigure, path: Path, banner: str = "") -> Path:
    """Spectrum figure in the format given by the file suffix (.svg, .pdf, .png)."""
    fig = Figure(figsize=(10, 5))
    draw_spectrum(fig, spec, (0.08, 0.12, 0.88, 0.76))
    if banner:
        fig.text(0.5, 0.95, banner, ha="center", va="center", fontsize=9, weight="bold")
    fig.savefig(path, dpi=150, metadata=_metadata(path))
    return path


def _metadata(path: Path) -> dict[str, str | None]:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return {"Software": "NSW"}
    if suffix == ".svg":
        return {"Creator": "NSW", "Date": None}  # no timestamp: reproducible output
    return {"Creator": "NSW", "Producer": "NSW", "CreationDate": None}


def _lines(section: Section) -> list[tuple[str, str]]:
    """(style, text) lines of one section: 'head', 'key', 'text', 'mono'."""
    out: list[tuple[str, str]] = [("head", section.title)]
    width = max((len(k) for k, _ in section.rows), default=0)
    width = min(width, 32)
    for key, value in section.rows:
        wrapped = textwrap.wrap(str(value), WRAP - width - 3) or [""]
        out.append(("key", f"{key[:width]:<{width}}   {wrapped[0]}"))
        out.extend(("key", " " * (width + 3) + w) for w in wrapped[1:])
    if section.table is not None:
        headers, rows = section.table
        cells = [list(map(str, headers)), *[list(map(str, r)) for r in rows]]
        widths = [min(max(len(r[i]) for r in cells if i < len(r)), 28) for i in range(len(headers))]
        if sum(widths) + 2 * len(widths) <= TABLE_WIDTH:
            for i, row in enumerate(cells):
                text = "  ".join(
                    (c[: widths[j] - 1] + "…" if len(c) > widths[j] else c).ljust(widths[j])
                    for j, c in enumerate(row)
                )
                out.append(("mono" if i else "monohead", text))
        else:  # too wide for the page: one block per row, nothing cut off
            for row in cells[1:]:
                out.append(("monohead", f"{headers[0]}: {row[0]}"))
                pairs = "; ".join(f"{h}: {v}" for h, v in zip(headers[1:], row[1:], strict=False))
                out.extend(("mono", "    " + w) for w in textwrap.wrap(pairs, TABLE_WIDTH - 4))
    if section.text:
        for paragraph in section.text.split("\n"):
            out.extend(("text", w) for w in textwrap.wrap(paragraph, WRAP) or [""])
    out.append(("text", ""))
    return out


def write_pdf(report: Report, path: Path) -> Path:
    """Write ``report`` as an A4 PDF. Returns the path."""
    colour = BANNER_COLOURS.get(report.banner_kind, "#333333")
    with PdfPages(path, metadata={"Title": report.title, "Creator": "NSW",
                                  "CreationDate": None}) as pdf:  # fmt: skip
        page_no = 0

        def new_page() -> tuple[Figure, float]:
            nonlocal page_no
            page_no += 1
            fig = Figure(figsize=PAGE)
            fig.text(MARGIN_X, 0.975, report.title, fontsize=11, weight="bold", va="center")
            fig.text(
                0.5, 0.955, report.banner, fontsize=8, weight="bold", color="white",
                ha="center", va="center",
                bbox={"facecolor": colour, "edgecolor": colour, "boxstyle": "square,pad=0.4"},
            )  # fmt: skip
            fig.text(1 - MARGIN_X, 0.02, f"page {page_no}", fontsize=6, ha="right")
            if report.footer:
                fig.text(MARGIN_X, 0.02, report.footer, fontsize=6)
            return fig, TOP - 0.03

        fig, y = new_page()
        for spec in (report.figure, *report.extra_figures):
            if spec is None:
                continue
            height = 0.3
            if y - height < BOTTOM + 0.05:
                pdf.savefig(fig)
                fig, y = new_page()
            draw_spectrum(
                fig, spec, (MARGIN_X + 0.05, y - height, 1 - 2 * MARGIN_X - 0.05, height - 0.03)
            )
            y -= height + 0.04
        for section in report.sections:
            lines = _lines(section)
            if y - LINE * min(len(lines), 4) < BOTTOM:
                pdf.savefig(fig)
                fig, y = new_page()
            for style, text in lines:
                if y < BOTTOM:
                    pdf.savefig(fig)
                    fig, y = new_page()
                kwargs: dict[str, object] = {"fontsize": 7, "va": "top"}
                if style == "head":
                    kwargs.update(fontsize=9, weight="bold")
                elif style in ("mono", "monohead", "key"):
                    kwargs.update(family="monospace", fontsize=6.3)
                    if style == "monohead":
                        kwargs.update(weight="bold")
                fig.text(MARGIN_X, y, text, **kwargs)  # type: ignore[arg-type]
                y -= LINE * (1.4 if style == "head" else 1.0)
        pdf.savefig(fig)
    return path
