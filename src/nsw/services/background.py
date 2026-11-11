"""Use case: background subtraction and background comparison, with readable summaries."""

from __future__ import annotations

from nsw.analysis.background import (
    BackgroundSubtraction,
    Compatibility,
    RateComparison,
    check_compatibility,
    compare_count_rates,
    subtract_background,
)
from nsw.core.errors import ProcessingError
from nsw.core.spectrum import Spectrum

__all__ = [
    "BackgroundSubtraction",
    "Compatibility",
    "ProcessingError",
    "RateComparison",
    "check",
    "compare",
    "comparison_text",
    "result_rows",
    "subtract",
]

Rows = list[tuple[str, str]]


def check(gross: Spectrum, background: Spectrum) -> Compatibility:
    return check_compatibility(gross, background)


def subtract(gross: Spectrum, background: Spectrum) -> BackgroundSubtraction:
    return subtract_background(gross, background)


def compare(a: Spectrum, b: Spectrum) -> RateComparison:
    return compare_count_rates(a, b)


def _name(s: Spectrum) -> str:
    return s.source.filename if s.source else (s.synthetic_id or "in-memory spectrum")


def result_rows(result: BackgroundSubtraction) -> Rows:
    g, b, net = result.gross, result.background, result.net
    total = result.region(net.first_channel, net.first_channel + net.n_channels - 1)
    rows: Rows = [
        ("Gross spectrum", f"{_name(g)}  (live {g.live_time_s:g} s, {g.total:,.0f} counts)"),
        ("Background spectrum", f"{_name(b)}  (live {b.live_time_s:g} s, {b.total:,.0f} counts)"),
        ("Equation", "N_net = N_gross − α · N_background"),
        ("Variance", "σ²_net = N_gross + α² · N_background  (independent Poisson counts)"),
        ("Scaling factor α", f"{result.alpha:.9g}  (gross live time / background live time)"),
        (
            "α systematic uncertainty",
            f"{result.alpha_sys:.3g}  (from rounded live times; correlated over channels)"
            if result.alpha_sys > 0
            else "0 (live times recorded with full precision)",
        ),
        ("Net counts, all channels", total.format()),
        (
            "  statistical / systematic",
            f"± {total.stat:,.1f} (Poisson)  /  ± {total.sys:,.2f} (scaling factor)",
        ),
        (
            "Channels with negative net value",
            f"{int((net.counts < 0).sum())} of {net.n_channels} (kept, not clipped)",
        ),
        ("Net spectrum data SHA-256", net.data_sha256),
    ]
    rows.extend(("Caution", w) for w in result.compatibility.warnings)
    rows.append(("Assumption", net.provenance[-1].parameters["assumption"]))
    return rows


def comparison_text(a: Spectrum, b: Spectrum) -> str:
    """One-line summary of the rate comparison of two spectra, or the reason it is refused."""
    try:
        c = compare(a, b)
    except ProcessingError as exc:
        return f"not comparable: {exc}"
    return (
        f"rates {c.rate_a.format()} vs {c.rate_b.format()};  "
        f"χ² = {c.chi2:.1f} for {c.ndf} blocks of {c.block_width} channels "
        f"(p = {c.p_value:.3g})"
    )
