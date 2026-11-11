"""Generate the standard synthetic test spectra into data/synthetic/{hpge,nai}/.

    .venv\\Scripts\\python.exe scripts\\generate_synthetic.py

Deterministic: re-running reproduces identical files; an existing file with different
content is never overwritten (the script stops instead).
"""

from __future__ import annotations

import sys
from pathlib import Path

from nsw.simulation.generator import generate, write_synthetic
from nsw.simulation.presets import hpge_scenarios, nai_scenarios

ROOT = Path(__file__).resolve().parents[1] / "data" / "synthetic"


def main() -> int:
    for folder, specs in (("hpge", hpge_scenarios()), ("nai", nai_scenarios())):
        for spec in specs:
            result = generate(spec)
            path = write_synthetic(result, ROOT / folder)
            print(f"{path.relative_to(ROOT.parent.parent)}  total={result.truth['total_counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
