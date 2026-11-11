"""Create or verify a SHA-256 manifest of a read-only experimental dataset.

Standard library only. The dataset is opened strictly for reading.

    python scripts/verify_dataset_integrity.py create <dataset_root> <manifest.csv>
    python scripts/verify_dataset_integrity.py verify <dataset_root> <manifest.csv>

`verify` exits with status 1 if any file was added, removed or changed.
"""

from __future__ import annotations

import csv
import hashlib
import sys
from pathlib import Path


def scan(root: Path) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[path.relative_to(root).as_posix()] = (path.stat().st_size, digest)
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] not in {"create", "verify"}:
        print(__doc__)
        return 2
    mode, root, manifest = argv[1], Path(argv[2]), Path(argv[3])
    current = scan(root)
    if mode == "create":
        if manifest.exists():
            print(f"refusing to overwrite existing manifest: {manifest}")
            return 2
        with manifest.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["relative_path", "size_bytes", "sha256"])
            for rel, (size, digest) in current.items():
                writer.writerow([rel, size, digest])
        print(f"manifest written: {len(current)} files")
        return 0
    with manifest.open(newline="", encoding="utf-8") as fh:
        recorded = {
            r["relative_path"]: (int(r["size_bytes"]), r["sha256"]) for r in csv.DictReader(fh)
        }
    added = sorted(set(current) - set(recorded))
    removed = sorted(set(recorded) - set(current))
    changed = sorted(k for k in set(current) & set(recorded) if current[k] != recorded[k])
    for label, items in (("ADDED", added), ("REMOVED", removed), ("CHANGED", changed)):
        for item in items:
            print(f"{label}: {item}")
    ok = not (added or removed or changed)
    print(f"{len(current)} files scanned, {len(recorded)} recorded: {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
