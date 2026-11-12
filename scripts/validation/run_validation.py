"""Consolidated synthetic validation run (see ``nsw.services.validation_suite``).

    .venv\\Scripts\\python.exe scripts\\validation\\run_validation.py [--trials 200] [--seed 50000]
                                                         [--repeat] [--out reports]

Writes ``validation_run.md`` and ``validation_run.json`` into the output folder
(default ``reports/``, git-ignored) and exits with status 1 if a checked metric fails.
``--repeat`` runs the suite a second time with the same seed (must be bit-identical)
and a third time with another seed (must pass as well). Synthetic data only.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from pathlib import Path

from nsw.services.validation_suite import repeatability, run_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--seed", type=int, default=50_000)
    parser.add_argument("--repeat", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    started = time.monotonic()
    run = run_suite(args.trials, args.seed, progress=lambda name: print(f"running: {name}"))
    if args.repeat:
        print("repeating with the same seed")
        again = run_suite(args.trials, args.seed)
        print("repeating with another seed")
        other = run_suite(args.trials, args.seed + 1_000_000)
        extra = (*repeatability(run, again), *repeatability(run, other))
        run = replace(run, checks=(*run.checks, *extra))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "validation_run.md").write_text(run.to_markdown(), encoding="utf-8", newline="\n")
    (args.out / "validation_run.json").write_text(run.to_json(), encoding="utf-8", newline="\n")
    print(run.to_markdown())
    print(f"{time.monotonic() - started:.0f} s; written to {args.out}")
    return 1 if run.failed else 0


if __name__ == "__main__":
    sys.exit(main())
