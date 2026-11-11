"""SHA-256 manifests for read-only experimental dataset directories."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    scanned: int
    recorded: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not (self.added or self.removed or self.changed)


def scan_hashes(root: Path) -> dict[str, tuple[int, str]]:
    """Relative POSIX path -> (size, sha256) for every file under ``root`` (read-only)."""
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        with path.open("rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        result[path.relative_to(root).as_posix()] = (path.stat().st_size, digest)
    return result


def read_manifest(manifest: Path) -> dict[str, tuple[int, str]]:
    with manifest.open(newline="", encoding="utf-8") as fh:
        return {
            row["relative_path"].replace("\\", "/"): (int(row["size_bytes"]), row["sha256"])
            for row in csv.DictReader(fh)
        }


def create_manifest(root: Path, manifest: Path) -> int:
    """Write a new manifest. Refuses to overwrite an existing one."""
    if manifest.exists():
        raise FileExistsError(f"refusing to overwrite existing manifest: {manifest}")
    current = scan_hashes(root)
    with manifest.open("x", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["relative_path", "size_bytes", "sha256"])
        for rel, (size, digest) in current.items():
            writer.writerow([rel, size, digest])
    return len(current)


def verify_manifest(root: Path, manifest: Path) -> IntegrityReport:
    current, recorded = scan_hashes(root), read_manifest(manifest)
    return IntegrityReport(
        scanned=len(current),
        recorded=len(recorded),
        added=tuple(sorted(set(current) - set(recorded))),
        removed=tuple(sorted(set(recorded) - set(current))),
        changed=tuple(sorted(k for k in set(current) & set(recorded) if current[k] != recorded[k])),
    )
