"""Application settings loaded from ``config/local.yaml`` (git-ignored, personal paths).

This module sits outside the scientific layers: ``nsw.core``, ``nsw.analysis``,
``nsw.io``, ``nsw.detectors`` and ``nsw.simulation`` never import it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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


class DetectionLimitSettings(BaseModel):
    """Default method and probabilities for decision thresholds and detection limits.

    ``iso11929``: Bayesian framework of the ISO 11929 series (conformity not verified);
    ``currie``: classical Currie (1968) limits. See ``nsw.analysis.limits``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["iso11929", "currie"] = "iso11929"
    alpha: float = Field(default=0.05, gt=0.0, lt=0.5)
    beta: float = Field(default=0.05, gt=0.0, lt=0.5)
    gamma: float = Field(default=0.05, gt=0.0, lt=0.5)


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    detection_limits: DetectionLimitSettings = DetectionLimitSettings()


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    paths: PathSettings
    analysis: AnalysisSettings = AnalysisSettings()

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


def user_data_dir() -> Path:
    """Per-user folder of the installed application (settings); never removed by the
    uninstaller. A source checkout uses config/local.yaml in the project instead."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "NSW"


def default_settings_path() -> Path:
    if getattr(sys, "frozen", False):  # installed / packaged application
        return user_data_dir() / "local.yaml"
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
