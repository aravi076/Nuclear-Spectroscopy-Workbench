"""Check that relative links and backticked repository paths in Markdown files exist.

    .venv\\Scripts\\python.exe scripts\\check_links.py

Checks ``[text](relative/path)`` links in every tracked ``.md`` file and backticked
references of the form ``docs/...``, ``src/...``, ``scripts/...``, ``tests/...``,
``packaging/...`` and ``config/...``. Exit status 1 lists the broken ones.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
# personal, git-ignored files: documented, but never part of a checkout
PERSONAL = {"config/local.yaml"}
TICKED = re.compile(r"`((?:docs|src|scripts|tests|packaging|config)/[A-Za-z0-9_./\-]+)`")


def main() -> int:
    files = subprocess.run(  # noqa: S603 - fixed argument list
        ["git", "-C", str(ROOT), "ls-files", "*.md"],  # noqa: S607 - git from PATH
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    broken = []
    for f in files:
        if f.startswith("graphify-out"):
            continue
        path = ROOT / f
        text = path.read_text(encoding="utf-8")
        for target in LINK.findall(text):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).exists():
                broken.append(f"{f}: link {target}")
        for ref in TICKED.findall(text):
            ref = ref.rstrip(".")
            if "*" in ref or "<" in ref or ref in PERSONAL:
                continue
            if not (ROOT / ref).exists():
                broken.append(f"{f}: path {ref}")
    for b in broken:
        print(b)
    print(f"{len(broken)} broken reference(s) in {len(files)} Markdown files")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
