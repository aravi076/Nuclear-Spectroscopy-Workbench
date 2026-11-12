"""Monte Carlo validation tables for peak search and fitting (synthetic data only).

    .venv\\Scripts\\python.exe scripts\\validation\\validate_peak_fitting.py [n_trials]

Prints Markdown tables (used in VALIDATION_REPORT.md). Deterministic for a given
n_trials: every trial uses a fixed seed. Definitions of the metrics are in
``nsw.services.validation``.
"""

from __future__ import annotations

import sys

import numpy as np

import nsw
from nsw.services.validation import (
    STANDARD_DETECTION_CASES,
    STANDARD_FIT_CASES,
    run_detection_study,
    run_fit_study,
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    print(f"nsw {nsw.__version__}, numpy {np.__version__}, {n} trials per case\n")
    print(
        "| Case | method | area | bkg/ch | FWHM ch | failure rate | area rel. bias (± s.e.) | "
        "area pull mean / sd | coverage 1σ area / position / width | coverage 2σ area | "
        "p<0.05 |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for case in STANDARD_FIT_CASES:
        r = run_fit_study(case, n)
        a = r.area
        print(
            f"| {case.name} | {case.method} | {case.area:g} | {case.background_per_channel:g} | "
            f"{case.fwhm_channels:g} | {100 * r.failure_rate:.1f} % | "
            f"{100 * a.relative_bias:+.2f} % ± {100 * a.bias_se / a.truth:.2f} % | "
            f"{a.pull_mean:+.2f} / {a.pull_sd:.2f} | "
            f"{a.coverage_1:.3f} / {r.centroid.coverage_1:.3f} / {r.sigma.coverage_1:.3f} | "
            f"{a.coverage_2:.3f} | {r.false_alarm_rate:.3f} |"
        )
    print()
    print(
        "| area | bkg/ch | FWHM ch | scales | threshold | detection probability (± s.e.) | "
        "false candidates per 1000 channels |"
    )
    print("|---|---|---|---|---|---|---|")
    for area, bkg, fwhm, scales in STANDARD_DETECTION_CASES:
        d = run_detection_study(area, bkg, fwhm, scales, n_trials=n)
        print(
            f"| {area:g} | {bkg:g} | {fwhm:g} | {scales} | {d.threshold:g} | "
            f"{d.detection_probability:.3f} ± {d.detection_se:.3f} | "
            f"{d.false_per_1000_channels:.3f} |"
        )


if __name__ == "__main__":
    main()
