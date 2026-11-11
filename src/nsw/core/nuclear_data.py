"""Source-independent model of evaluated nuclear decay data (records only, no values).

This module contains **no nuclear data**. It defines how a record is stored so that
its origin can never be lost:

* every :class:`NuclideRecord` belongs to exactly one evaluation (``Provenance``): DDEP,
  ENSDF, or a fictitious test set. Records of different evaluations are never merged;
  a :class:`ReferenceLibrary` may hold both for one nuclide and the caller chooses by an
  explicit order of preference (:meth:`ReferenceLibrary.select`);
* a gamma intensity carries its **convention**. Only ``PER_DECAY`` (photons per decay
  of the parent) is an emission probability. A ``RELATIVE`` intensity has no
  established normalisation and :attr:`GammaRecord.emission_probability` refuses it;
* a value that the source does not give is ``None``, never a guess;
* ``original`` keeps source fields verbatim that the model does not interpret.

Units: energies in keV, half-lives in seconds, intensities as stated by the convention.
Uncertainties are one standard uncertainty.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nsw.core.errors import ProcessingError

MODEL_VERSION = 1
_NUCLIDE = re.compile(r"^([A-Z][a-z]?|SYN|DECOY)-([0-9A-Za-z]{1,6})(m[1-3]?)?$")


class IntensityConvention(StrEnum):
    PER_DECAY = "photons-per-decay"  # absolute emission probability per parent decay
    RELATIVE = "relative"  # relative to another line; normalisation not established


class ReuseStatus(StrEnum):
    """What NSW may do with the data of a provenance record."""

    FICTITIOUS = "fictitious"  # invented test values; not nuclear data
    BUNDLING_PERMITTED = "bundling-permitted"  # terms checked: may ship with NSW
    USER_SUPPLIED = "user-supplied"  # imported by the user; not redistributed by NSW
    UNVERIFIED = "unverified"  # terms not established: local use only, never bundled


class RadiationOrigin(StrEnum):
    GAMMA = "gamma"
    XRAY = "x-ray"
    ANNIHILATION = "annihilation"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Provenance(_Model):
    """Where one evaluation of one nuclide comes from. All text is quoted from the source
    or from the person who imported it; nothing is filled in by NSW."""

    evaluation: str = Field(min_length=1)  # "DDEP", "ENSDF", "FICTITIOUS", ...
    dataset_id: str = Field(min_length=1)  # identifier of the dataset within the source
    citation: str = Field(min_length=1)
    licence: str = Field(min_length=1)  # licence or reuse terms as stated by the source
    reuse_status: ReuseStatus
    retrieval_date: date
    evaluation_date: str | None = None  # as given by the source (may be partial)
    url: str | None = None
    accessed_through: str | None = None  # interface used, when not the evaluator's own
    parser: str = Field(min_length=1)  # importer name and version
    raw_sha256: str | None = None  # hash of the original file the record was read from
    transformations: tuple[str, ...] = ()  # unit conversions etc. applied on import

    @model_validator(mode="after")
    def _check(self) -> Any:
        fictitious = self.reuse_status is ReuseStatus.FICTITIOUS
        if fictitious != (self.evaluation.upper() == "FICTITIOUS"):
            raise ProcessingError(
                "evaluation 'FICTITIOUS' and reuse status 'fictitious' must be used together"
            )
        if self.raw_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.raw_sha256):
            raise ProcessingError("raw_sha256 must be a lower-case SHA-256 hex digest")
        return self

    @property
    def fictitious(self) -> bool:
        return self.reuse_status is ReuseStatus.FICTITIOUS


class GammaRecord(_Model):
    """One photon line of a nuclide as given by one evaluation."""

    energy_kev: float = Field(gt=0.0)
    energy_unc_kev: float | None = Field(default=None, ge=0.0)
    intensity: float = Field(gt=0.0)
    intensity_unc: float | None = Field(default=None, ge=0.0)
    convention: IntensityConvention
    origin: RadiationOrigin = RadiationOrigin.GAMMA
    transition: str = ""  # identity of the transition as given by the source
    emitted_by: str = ""  # daughter/level the photon follows, as given by the source
    comment: str = ""

    @model_validator(mode="after")
    def _check(self) -> Any:
        values = (self.energy_kev, self.intensity, self.energy_unc_kev, self.intensity_unc)
        if not all(math.isfinite(v) for v in values if v is not None):
            raise ProcessingError("line values must be finite")
        if self.convention is IntensityConvention.PER_DECAY and self.intensity > 2.0 + 1e-12:
            raise ProcessingError(
                f"{self.intensity} photons per decay at {self.energy_kev} keV is not a "
                "probability per decay: is this a percentage or a relative intensity?"
            )
        return self

    @property
    def is_absolute(self) -> bool:
        return self.convention is IntensityConvention.PER_DECAY

    @property
    def emission_probability(self) -> float:
        """Photons per decay. Raises for relative intensities: their normalisation is
        not established and they must not be used as emission probabilities."""
        if not self.is_absolute:
            raise ProcessingError(
                f"the line at {self.energy_kev} keV has a relative intensity; it cannot be "
                "used as an emission probability without the evaluation's normalisation"
            )
        return self.intensity


class DecayBranch(_Model):
    mode: str = Field(min_length=1)  # as named by the source: "beta-", "EC", "alpha", ...
    daughter: str | None = None
    fraction: float | None = Field(default=None, gt=0.0, le=1.0)
    fraction_unc: float | None = Field(default=None, ge=0.0)


class NuclideRecord(_Model):
    """One evaluation of one nuclide."""

    nuclide: str  # "Co-60", "Ba-137m"; fictitious: "SYN-CAL", "DECOY-A"
    provenance: Provenance
    half_life_s: float | None = Field(default=None, gt=0.0)
    half_life_unc_s: float | None = Field(default=None, ge=0.0)
    half_life_as_stated: str = ""  # original value and unit, verbatim
    branches: tuple[DecayBranch, ...] = ()
    lines: tuple[GammaRecord, ...] = ()
    comment: str = ""
    original: Mapping[str, str] = Field(default_factory=dict)  # uninterpreted source fields

    @model_validator(mode="after")
    def _check(self) -> Any:
        if not _NUCLIDE.fullmatch(self.nuclide):
            raise ProcessingError(
                f"nuclide {self.nuclide!r} is not of the form 'Co-60' or 'Ba-137m'"
            )
        named_fictitious = self.nuclide.startswith(("SYN-", "DECOY-"))
        if named_fictitious != self.provenance.fictitious:
            raise ProcessingError(
                f"{self.nuclide}: fictitious names (SYN-, DECOY-) and fictitious provenance "
                "must be used together"
            )
        if self.half_life_unc_s is not None and self.half_life_s is None:
            raise ProcessingError(f"{self.nuclide}: half-life uncertainty without a half-life")
        energies = [(line.energy_kev, line.origin, line.transition) for line in self.lines]
        if len(set(energies)) != len(energies):
            raise ProcessingError(f"{self.nuclide}: the same line is listed twice")
        return self

    @property
    def key(self) -> tuple[str, str]:
        return self.nuclide, self.provenance.evaluation

    @property
    def absolute_lines(self) -> tuple[GammaRecord, ...]:
        return tuple(line for line in self.lines if line.is_absolute)

    @property
    def has_relative_lines(self) -> bool:
        return any(not line.is_absolute for line in self.lines)


class EvaluationConflict(_Model):
    nuclide: str
    evaluations: tuple[str, ...]
    differences: tuple[str, ...]


class ReferenceLibrary(_Model):
    """A set of nuclide records, at most one per (nuclide, evaluation)."""

    name: str = Field(min_length=1)
    records: tuple[NuclideRecord, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Any:
        keys = [r.key for r in self.records]
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ProcessingError(
                "more than one record for " + ", ".join(f"{n} ({e})" for n, e in duplicates)
            )
        kinds = {r.provenance.fictitious for r in self.records}
        if len(kinds) > 1:
            raise ProcessingError(
                "fictitious test records and real nuclear data cannot share a library"
            )
        return self

    @property
    def fictitious(self) -> bool:
        return bool(self.records) and self.records[0].provenance.fictitious

    @property
    def evaluations(self) -> tuple[str, ...]:
        return tuple(sorted({r.provenance.evaluation for r in self.records}))

    @property
    def nuclides(self) -> tuple[str, ...]:
        return tuple(sorted({r.nuclide for r in self.records}))

    def records_for(self, nuclide: str) -> tuple[NuclideRecord, ...]:
        return tuple(r for r in self.records if r.nuclide == nuclide)

    def select(self, preference: Sequence[str]) -> tuple[NuclideRecord, ...]:
        """One record per nuclide: the first evaluation of ``preference`` that has it.

        Values are never combined across evaluations. A nuclide that only evaluations
        outside ``preference`` cover is left out; an empty preference is an error (the
        choice of evaluation must be explicit).
        """
        if not preference:
            raise ProcessingError("state which evaluations to use, in order of preference")
        chosen: list[NuclideRecord] = []
        for nuclide in self.nuclides:
            by_evaluation = {r.provenance.evaluation: r for r in self.records_for(nuclide)}
            for evaluation in preference:
                if evaluation in by_evaluation:
                    chosen.append(by_evaluation[evaluation])
                    break
        return tuple(chosen)

    def merged_with(self, records: Iterable[NuclideRecord]) -> ReferenceLibrary:
        """A library with ``records`` added (duplicates of nuclide+evaluation refused)."""
        return ReferenceLibrary(name=self.name, records=(*self.records, *records))

    def conflicts(self, rel_tol: float = 0.0) -> tuple[EvaluationConflict, ...]:
        """Nuclides covered by several evaluations, with what differs between them.

        Informational: nothing is reconciled. Half-lives and the line lists (energies,
        intensities of absolute lines) are compared; ``rel_tol`` is a relative tolerance
        below which two numbers count as equal.
        """
        out: list[EvaluationConflict] = []
        for nuclide in self.nuclides:
            recs = self.records_for(nuclide)
            if len(recs) < 2:
                continue
            first, differences = recs[0], []
            for other in recs[1:]:
                pair = f"{first.provenance.evaluation} / {other.provenance.evaluation}"
                if not _same(first.half_life_s, other.half_life_s, rel_tol):
                    differences.append(
                        f"half-life ({pair}): {first.half_life_s} s / {other.half_life_s} s"
                    )
                a = {round(line.energy_kev, 2): line for line in first.absolute_lines}
                b = {round(line.energy_kev, 2): line for line in other.absolute_lines}
                only = sorted(set(a) ^ set(b))
                if only:
                    differences.append(
                        f"lines listed by only one of {pair}: "
                        + ", ".join(f"{e:g} keV" for e in only)
                    )
                for e in sorted(set(a) & set(b)):
                    if not _same(a[e].intensity, b[e].intensity, rel_tol):
                        differences.append(
                            f"emission probability at {e:g} keV ({pair}): "
                            f"{a[e].intensity} / {b[e].intensity}"
                        )
            out.append(
                EvaluationConflict(
                    nuclide=nuclide,
                    evaluations=tuple(r.provenance.evaluation for r in recs),
                    differences=tuple(differences),
                )
            )
        return tuple(out)


def _same(a: float | None, b: float | None, rel_tol: float) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=0.0)
