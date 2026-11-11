"""Use case: check a read-only dataset against its recorded SHA-256 manifest."""

from __future__ import annotations

from pathlib import Path

from nsw.io.integrity import IntegrityReport, verify_manifest

__all__ = ["IntegrityReport", "verify_dataset"]


def verify_dataset(root: Path, manifest: Path) -> IntegrityReport:
    return verify_manifest(root, manifest)
