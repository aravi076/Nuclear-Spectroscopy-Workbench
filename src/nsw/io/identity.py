"""Measurement identity: which files hold the same data, and which data are
successive saves of one running acquisition.

Identity is established from content and acquisition metadata, never from file names:

* **Snapshot** - all files whose channel data are identical (same data hash). A snapshot
  is one saved state of the MCA memory, possibly exported in several formats.
* **Acquisition** - all snapshots sharing one acquisition start time (within
  ``START_TOLERANCE_S``). If, ordered by live time, the counts never decrease in any
  channel, the earlier snapshots are intermediate saves of the same running acquisition
  and only the last one counts as an independent measurement.

File names are used solely to *report* disagreements between a name and the content.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath

import numpy as np

from nsw.core.spectrum import MeasurementStatus, Spectrum

START_TOLERANCE_S = 1.0
# Preference when one snapshot exists in several formats (TECHNICAL_DECISIONS TD-006).
_FORMAT_RANK = {"LynxAvaAdapter": 0, "LynxCsvAdapter": 1, "CanberraCnfAdapter": 2}
_DURATION_IN_NAME = re.compile(r"(\d+)\s*s(?:ec?)?(?![A-Za-z])", re.IGNORECASE)


class FindingKind(StrEnum):
    DUPLICATE_FILE = "duplicate-file"  # byte-identical files
    NAME_CONTENT_CONFLICT = "name-content-conflict"  # same stem, different data
    DURATION_NAME_MISMATCH = "duration-name-mismatch"  # name suggests another live time
    SUCCESSIVE_SAVES = "successive-saves"
    INCONSISTENT_SERIES = "inconsistent-series"  # same start time but not cumulative
    TIMING_DISAGREEMENT = "timing-disagreement"  # same data, different recorded times
    START_TIME_DISAGREEMENT = "start-time-disagreement"  # same data, different start times


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    message: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    """One saved MCA state; ``files`` are all imports with identical channel data."""

    data_sha256: str
    files: tuple[Spectrum, ...]
    preferred: Spectrum  # best-documented member, with measurement_status assigned

    @property
    def live_time_s(self) -> float | None:
        return self.preferred.live_time_s

    @property
    def filenames(self) -> tuple[str, ...]:
        return tuple(s.source.filename for s in self.files if s.source)


@dataclass(frozen=True)
class Acquisition:
    """One physical acquisition: one or more snapshots sharing a start time."""

    start_time_local: datetime | None
    snapshots: tuple[Snapshot, ...]  # ordered by live time
    cumulative_verified: bool  # True if counts are non-decreasing along the series

    @property
    def final(self) -> Snapshot:
        return self.snapshots[-1]

    @property
    def is_series(self) -> bool:
        return len(self.snapshots) > 1


@dataclass(frozen=True)
class IdentityReport:
    acquisitions: tuple[Acquisition, ...]
    findings: tuple[Finding, ...]

    @property
    def snapshots(self) -> tuple[Snapshot, ...]:
        return tuple(s for a in self.acquisitions for s in a.snapshots)

    @property
    def n_independent(self) -> int:
        return sum(
            1
            for s in self.snapshots
            if s.preferred.measurement_status is MeasurementStatus.INDEPENDENT
        )

    def findings_of(self, kind: FindingKind) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.kind is kind)


def _path(s: Spectrum) -> str:
    return s.source.path if s.source else (s.synthetic_id or "?")


def _preference(s: Spectrum) -> tuple[int, int, str]:
    adapter = s.source.adapter if s.source else ""
    rounded = s.time_quantum_s > 0
    rank = _FORMAT_RANK.get(adapter, 9)
    if rounded and adapter == "LynxCsvAdapter":
        # A Lynx CSV with whole-second times is a re-export of a CNF file (the direct
        # export carries microsecond times); the CNF is then the more original record.
        rank = 3
    return (int(rounded), rank, _path(s))


def _live_sort_key(s: Spectrum) -> tuple[float, float]:
    return (s.live_time_s or 0.0, s.total)


def analyse_identity(spectra: list[Spectrum]) -> IdentityReport:
    """Group imported spectra into snapshots and acquisitions and report inconsistencies."""
    findings: list[Finding] = []

    # ---- byte-identical files
    by_file_hash: dict[str, list[Spectrum]] = defaultdict(list)
    for s in spectra:
        if s.source:
            by_file_hash[s.source.sha256].append(s)
    for group in by_file_hash.values():
        if len(group) > 1:
            findings.append(
                Finding(
                    FindingKind.DUPLICATE_FILE,
                    "files are byte-identical: " + ", ".join(_name(s) for s in group),
                    tuple(_path(s) for s in group),
                )
            )

    # ---- snapshots: identical channel data
    by_data: dict[str, list[Spectrum]] = defaultdict(list)
    for s in spectra:
        by_data[s.data_sha256].append(s)
    raw_snapshots: list[tuple[str, list[Spectrum], Spectrum]] = []
    for sha, members in by_data.items():
        members.sort(key=_preference)
        best = members[0]
        precise = [m for m in members if m.time_quantum_s == 0 and m.real_time_s is not None]
        if len(precise) > 1:
            reals = {round(m.real_time_s or 0.0, 3) for m in precise}
            lives = {round(m.live_time_s or 0.0, 3) for m in precise}
            if len(reals) > 1 or len(lives) > 1:
                findings.append(
                    Finding(
                        FindingKind.TIMING_DISAGREEMENT,
                        "identical channel data but different recorded times: "
                        + ", ".join(
                            f"{_name(m)} (live {m.live_time_s}, real {m.real_time_s})"
                            for m in precise
                        ),
                        tuple(_path(m) for m in precise),
                    )
                )
        starts = [m.start_time_local for m in members if m.start_time_local is not None]
        if starts and (max(starts) - min(starts)).total_seconds() > START_TOLERANCE_S:
            findings.append(
                Finding(
                    FindingKind.START_TIME_DISAGREEMENT,
                    "identical channel data but different recorded start times: "
                    + ", ".join(f"{_name(m)} ({m.start_time_local})" for m in members)
                    + f"; using {_name(best)}",
                    tuple(_path(m) for m in members),
                )
            )
        raw_snapshots.append((sha, members, best))

    # ---- acquisitions: cluster snapshots by start time
    timed = sorted(
        (t for t in raw_snapshots if t[2].start_time_local is not None),
        key=lambda t: t[2].start_time_local,  # type: ignore[arg-type,return-value]
    )
    untimed = [t for t in raw_snapshots if t[2].start_time_local is None]
    clusters: list[list[tuple[str, list[Spectrum], Spectrum]]] = []
    for item in timed:
        start = item[2].start_time_local
        assert start is not None  # noqa: S101 - type narrowing: `timed` holds only timed items
        if clusters:
            prev = clusters[-1][-1][2].start_time_local
            assert prev is not None  # noqa: S101 - type narrowing: clusters hold only timed items
            if (start - prev).total_seconds() <= START_TOLERANCE_S:
                clusters[-1].append(item)
                continue
        clusters.append([item])
    clusters.extend([u] for u in untimed)

    acquisitions: list[Acquisition] = []
    for cluster in clusters:
        cluster.sort(key=lambda t: _live_sort_key(t[2]))
        cumulative = all(
            cluster[i][2].n_channels == cluster[i + 1][2].n_channels
            and bool(np.all(cluster[i + 1][2].counts >= cluster[i][2].counts))
            and (cluster[i + 1][2].live_time_s or 0.0) >= (cluster[i][2].live_time_s or 0.0)
            for i in range(len(cluster) - 1)
        )
        snaps: list[Snapshot] = []
        for i, (sha, members, best) in enumerate(cluster):
            if len(cluster) == 1 or (cumulative and i == len(cluster) - 1):
                status = MeasurementStatus.INDEPENDENT
            elif cumulative:
                status = MeasurementStatus.INTERMEDIATE_SAVE
            else:
                status = MeasurementStatus.UNCLASSIFIED
            snaps.append(Snapshot(sha, tuple(members), best.evolve(measurement_status=status)))
        start_local = cluster[0][2].start_time_local
        if len(cluster) > 1:
            live_text = ", ".join(f"{s.live_time_s:g} s" for s in snaps if s.live_time_s)
            names = tuple(_path(m) for s in snaps for m in s.files)
            if cumulative:
                findings.append(
                    Finding(
                        FindingKind.SUCCESSIVE_SAVES,
                        f"{len(cluster)} saves of one acquisition started {start_local} "
                        f"(live times {live_text}); counts are cumulative, so only the last "
                        "save is an independent measurement",
                        names,
                    )
                )
            else:
                findings.append(
                    Finding(
                        FindingKind.INCONSISTENT_SERIES,
                        f"{len(cluster)} different spectra share the start time {start_local} "
                        f"(live times {live_text}) but are not cumulative; their relationship "
                        "could not be established",
                        names,
                    )
                )
        acquisitions.append(Acquisition(start_local, tuple(snaps), cumulative))

    findings.extend(_name_findings(spectra))
    acquisitions.sort(
        key=lambda a: (a.start_time_local is None, a.start_time_local or datetime.min)
    )
    return IdentityReport(tuple(acquisitions), tuple(findings))


def _name(s: Spectrum) -> str:
    return s.source.filename if s.source else (s.synthetic_id or "?")


def _name_findings(spectra: list[Spectrum]) -> list[Finding]:
    """Disagreements between what a file is called and what it contains."""
    out: list[Finding] = []
    by_stem: dict[tuple[str, str], list[Spectrum]] = defaultdict(list)
    for s in spectra:
        if s.source:
            p = PurePath(s.source.path)
            by_stem[(str(p.parent).lower(), p.stem.lower())].append(s)
    for members in by_stem.values():
        if len({m.data_sha256 for m in members}) > 1:
            detail = "; ".join(
                f"{_name(m)}: start {m.start_time_local}, live {m.live_time_s} s, "
                f"total {m.total:.0f}"
                for m in members
            )
            out.append(
                Finding(
                    FindingKind.NAME_CONTENT_CONFLICT,
                    f"files with the same name hold different data ({detail})",
                    tuple(_path(m) for m in members),
                )
            )
    for s in spectra:
        if not s.source or s.live_time_s is None:
            continue
        tokens = [int(t) for t in _DURATION_IN_NAME.findall(PurePath(s.source.filename).stem)]
        if not tokens:
            continue
        tolerance = 0.01 * s.live_time_s + 1.0 + s.time_quantum_s
        if not any(abs(t - s.live_time_s) <= tolerance for t in tokens):
            out.append(
                Finding(
                    FindingKind.DURATION_NAME_MISMATCH,
                    f"{_name(s)}: file name suggests {tokens[-1]} s but the recorded live "
                    f"time is {s.live_time_s:g} s",
                    (_path(s),),
                )
            )
    return out
