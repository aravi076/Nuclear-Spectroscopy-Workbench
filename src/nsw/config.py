"""Application settings loaded from ``config/local.yaml`` (git-ignored, personal paths).

This module sits outside the scientific layers: ``nsw.core``, ``nsw.analysis``,
``nsw.io``, ``nsw.detectors`` and ``nsw.simulation`` never import it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from nsw.core.errors import NSWError


class ConfigurationError(NSWError):
    """The settings file is missing, unreadable or invalid."""


class PathSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    experimental_roots: tuple[Path, ...] = ()
    workspace: Path
    manifests: Path = Path("local")

    @field_validator("experimental_roots", "workspace", "manifests", mode="before")
    @classmethod
    def _no_empty(cls, v: Any) -> Any:
        if v in ("", None):
            raise ValueError("path must not be empty")
        return v


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    paths: PathSettings

    def is_inside_experimental_root(self, path: Path) -> bool:
        """True if ``path`` lies inside a read-only experimental dataset directory."""
        target = path.resolve()
        for root in self.paths.experimental_roots:
            try:
                target.relative_to(root.resolve())
            except ValueError:
                continue
            return True
        return False


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_settings_path() -> Path:
    return project_root() / "config" / "local.yaml"


def load_settings(path: Path | None = None) -> Settings:
    path = path or default_settings_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"settings file not found: {path} (copy config/nsw.example.yaml to config/local.yaml)"
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"settings file is not valid YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"settings file must contain a mapping: {path}")
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid settings in {path}:\n{exc}") from exc
