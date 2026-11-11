"""SQLite project store: one file per NSW project, append-only results.

What is stored
--------------
measurement          every spectrum added to the project: channel data (compressed),
                     variance for derived data, metadata, provenance, file identity and
                     the SHA-256 of the channel data. A stored spectrum is restored
                     bit for bit and its hash is checked on every read.
background_event     designations of a measurement as background, and their removal,
                     as an ordered event log (the current state is the last event)
calibration          energy, resolution and efficiency calibration records (JSON of the
                     validated record), with the spectrum they were made from
analysis_run         every analysis result (peak search, fit, identification, ...): kind,
                     input spectrum, settings, result, software version, and whether the
                     data were synthetic
reference_dataset    nuclear-data libraries imported into the project, with provenance
audit_log            what was done to the project, when

Rules
-----
* Results are never changed or deleted: triggers abort any UPDATE or DELETE on the
  result tables. Re-running an analysis adds a new row.
* The file records its schema version. A newer file is refused; an older one is copied
  to a timestamped backup before it is migrated.
* The store stores JSON-ready dictionaries given to it by the service layer; it does
  not know the analysis types (layering: ``database`` imports only ``core``).
"""

from __future__ import annotations

import io
import json
import sqlite3
import zlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from nsw.core.calibration import EnergyCalibration
from nsw.core.errors import NSWError
from nsw.core.spectrum import (
    DataOrigin,
    MeasurementStatus,
    ProcessingStep,
    SourceFile,
    Spectrum,
    SpectrumKind,
)

SCHEMA_VERSION = 1
APPLICATION_ID = 0x4E535731  # "NSW1": marks the file as an NSW project
RESULT_TABLES = (
    "measurement",
    "background_event",
    "calibration",
    "analysis_run",
    "reference_dataset",
    "audit_log",
)

_SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL, applied TEXT NOT NULL);
CREATE TABLE project (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    created TEXT NOT NULL,
    created_by_software TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE measurement (
    id INTEGER PRIMARY KEY,
    data_sha256 TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    n_channels INTEGER NOT NULL,
    record TEXT NOT NULL,
    counts BLOB NOT NULL,
    variance BLOB,
    added TEXT NOT NULL
);
CREATE TABLE background_event (
    id INTEGER PRIMARY KEY,
    data_sha256 TEXT NOT NULL REFERENCES measurement (data_sha256),
    action TEXT NOT NULL CHECK (action IN ('designate', 'remove')),
    note TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);
CREATE TABLE calibration (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('energy', 'resolution', 'efficiency')),
    data_sha256 TEXT REFERENCES measurement (data_sha256),
    status TEXT NOT NULL,
    synthetic INTEGER NOT NULL,
    record TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}',
    software TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE analysis_run (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    data_sha256 TEXT REFERENCES measurement (data_sha256),
    synthetic INTEGER NOT NULL,
    label TEXT NOT NULL,
    settings TEXT NOT NULL,
    result TEXT NOT NULL,
    software TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE reference_dataset (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    evaluations TEXT NOT NULL,
    fictitious INTEGER NOT NULL,
    document TEXT NOT NULL,
    document_sha256 TEXT NOT NULL,
    imported TEXT NOT NULL
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX analysis_by_spectrum ON analysis_run (data_sha256, kind);
CREATE INDEX calibration_by_spectrum ON calibration (data_sha256, kind);
"""


def _append_only_triggers() -> str:
    statements = []
    for table in RESULT_TABLES:
        for op in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only: results are never "
                f"changed or deleted'); END;"
            )
    return "\n".join(statements)


class ProjectStoreError(NSWError):
    """The project file is missing, foreign, newer than this NSW, or inconsistent."""


@dataclass(frozen=True, slots=True)
class StoredRun:
    id: int
    kind: str
    data_sha256: str | None
    synthetic: bool
    label: str
    settings: dict[str, Any]
    result: dict[str, Any]
    software: str
    created: str


@dataclass(frozen=True, slots=True)
class StoredCalibration:
    id: int
    kind: str
    data_sha256: str | None
    status: str
    synthetic: bool
    record: dict[str, Any]
    summary: dict[str, Any]
    software: str
    created: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    """Strict JSON: NaN and infinity are written as null-safe strings, never as bare
    tokens that other readers reject."""

    def clean(v: Any) -> Any:
        if isinstance(v, float) and not np.isfinite(v):
            return {"nonfinite": repr(v)}
        if isinstance(v, Mapping):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, (np.floating, np.integer)):
            return clean(v.item())
        if isinstance(v, np.ndarray):
            return clean(v.tolist())
        return v

    return json.dumps(clean(value), ensure_ascii=False, allow_nan=False, default=str)


def _unjson(text: str) -> Any:
    def restore(v: Any) -> Any:
        if isinstance(v, dict):
            if set(v) == {"nonfinite"}:
                return float(v["nonfinite"])
            return {k: restore(x) for k, x in v.items()}
        if isinstance(v, list):
            return [restore(x) for x in v]
        return v

    return restore(json.loads(text))


def _pack(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, np.ascontiguousarray(array), allow_pickle=False)
    return zlib.compress(buffer.getvalue(), 6)


def _unpack(blob: bytes) -> np.ndarray:
    array: np.ndarray = np.load(io.BytesIO(zlib.decompress(blob)), allow_pickle=False)
    return array


def _spectrum_record(s: Spectrum) -> dict[str, Any]:
    return {
        "kind": s.kind.value,
        "origin": s.origin.value,
        "live_time_s": s.live_time_s,
        "real_time_s": s.real_time_s,
        "start_time": s.start_time.isoformat() if s.start_time else None,
        "detector_id": s.detector_id,
        "sample_name": s.sample_name,
        "sample_description": s.sample_description,
        "first_channel": s.first_channel,
        "embedded_calibration": (
            s.embedded_calibration.model_dump(mode="json") if s.embedded_calibration else None
        ),
        "energy_calibration": (
            s.energy_calibration.model_dump(mode="json") if s.energy_calibration else None
        ),
        "source": (
            {
                "path": s.source.path,
                "filename": s.source.filename,
                "size_bytes": s.source.size_bytes,
                "sha256": s.source.sha256,
                "format": s.source.format,
                "adapter": s.source.adapter,
            }
            if s.source
            else None
        ),
        "synthetic_id": s.synthetic_id,
        "time_quantum_s": s.time_quantum_s,
        "measurement_status": s.measurement_status.value,
        "metadata": dict(s.metadata),
        "provenance": [
            {
                "operation": p.operation,
                "parameters": dict(p.parameters),
                "parents": list(p.parents),
                "note": p.note,
            }
            for p in s.provenance
        ],
        "warnings": list(s.warnings),
    }


def _spectrum_from(
    record: dict[str, Any], counts: np.ndarray, variance: np.ndarray | None, expected_sha256: str
) -> Spectrum:
    def calibration(data: dict[str, Any] | None) -> EnergyCalibration | None:
        return EnergyCalibration.model_validate(data) if data else None

    source = record["source"]
    spectrum = Spectrum(
        counts=counts,
        kind=SpectrumKind(record["kind"]),
        origin=DataOrigin(record["origin"]),
        live_time_s=record["live_time_s"],
        real_time_s=record["real_time_s"],
        start_time=datetime.fromisoformat(record["start_time"]) if record["start_time"] else None,
        detector_id=record["detector_id"],
        sample_name=record["sample_name"],
        sample_description=record["sample_description"],
        variance=variance,
        first_channel=record["first_channel"],
        embedded_calibration=calibration(record["embedded_calibration"]),
        energy_calibration=calibration(record["energy_calibration"]),
        source=SourceFile(**source) if source else None,
        synthetic_id=record["synthetic_id"],
        time_quantum_s=record["time_quantum_s"],
        measurement_status=MeasurementStatus(record["measurement_status"]),
        metadata=record["metadata"],
        provenance=tuple(
            ProcessingStep(p["operation"], p["parameters"], tuple(p["parents"]), p["note"])
            for p in record["provenance"]
        ),
        warnings=tuple(record["warnings"]),
    )
    if spectrum.data_sha256 != expected_sha256:
        raise ProjectStoreError(
            f"stored spectrum {expected_sha256[:12]}… does not reproduce its hash: the "
            "project file is damaged"
        )
    return spectrum


class ProjectStore:
    """An open NSW project file. Use :meth:`create` or :meth:`open`; close when done."""

    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._db = connection

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, path: Path, name: str, software: str, description: str = "") -> ProjectStore:
        path = Path(path)
        if path.exists():
            raise ProjectStoreError(f"file already exists: {path}")
        if not name.strip():
            raise ProjectStoreError("a project needs a name")
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            db.executescript(_SCHEMA + _append_only_triggers())
            with db:
                db.execute("INSERT INTO schema_version VALUES (?, ?)", (SCHEMA_VERSION, _now()))
                db.execute(
                    "INSERT INTO project VALUES (1, ?, ?, ?, ?)",
                    (name.strip(), _now(), software, description),
                )
                db.execute(
                    "INSERT INTO audit_log (at, action, detail) VALUES (?, ?, ?)",
                    (_now(), "create", f"project {name.strip()!r} created by nsw {software}"),
                )
        except Exception:
            db.close()
            path.unlink(missing_ok=True)
            raise
        return cls(path, db)

    @classmethod
    def open(cls, path: Path) -> ProjectStore:
        path = Path(path)
        if not path.is_file():
            raise ProjectStoreError(f"no project file at {path}")
        db = sqlite3.connect(path)
        try:
            (app_id,) = db.execute("PRAGMA application_id").fetchone()
            if app_id != APPLICATION_ID:
                raise ProjectStoreError(f"{path.name} is not an NSW project file")
            (version,) = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        except sqlite3.DatabaseError as exc:
            db.close()
            raise ProjectStoreError(f"{path.name} is not an NSW project file: {exc}") from exc
        except ProjectStoreError:
            db.close()
            raise
        if version > SCHEMA_VERSION:
            db.close()
            raise ProjectStoreError(
                f"{path.name} was written by a newer NSW (schema {version}; this NSW reads "
                f"up to {SCHEMA_VERSION}); it is left unchanged"
            )
        db.execute("PRAGMA foreign_keys = ON")
        store = cls(path, db)
        if version < SCHEMA_VERSION:  # pragma: no cover - no older schema exists yet
            store.backup(store._backup_name(f"before-schema-{SCHEMA_VERSION}"))
        store.log("open", f"schema {version}")
        return store

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> ProjectStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _backup_name(self, tag: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return self.path.with_name(f"{self.path.stem}.{tag}.{stamp}{self.path.suffix}")

    def backup(self, target: Path | None = None) -> Path:
        """Consistent copy of the whole project (SQLite online backup)."""
        target = Path(target) if target is not None else self._backup_name("backup")
        if target.exists():
            raise ProjectStoreError(f"backup target exists: {target}")
        copy = sqlite3.connect(target)
        try:
            self._db.backup(copy)
        finally:
            copy.close()
        self.log("backup", str(target.name))
        return target

    def export_copy(self, target: Path) -> Path:
        """A copy of the closed-state file for sharing (same as a backup)."""
        if Path(target).exists():
            raise ProjectStoreError(f"file already exists: {target}")
        return self.backup(Path(target))

    # ------------------------------------------------------------------ project
    @property
    def info(self) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT name, created, created_by_software, description FROM project"
        ).fetchone()
        (version,) = self._db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return {
            "name": row[0],
            "created": row[1],
            "created_by_software": row[2],
            "description": row[3],
            "schema_version": version,
            "path": str(self.path),
        }

    def log(self, action: str, detail: str = "") -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO audit_log (at, action, detail) VALUES (?, ?, ?)",
                (_now(), action, detail),
            )

    def audit_log(self) -> list[tuple[str, str, str]]:
        return list(self._db.execute("SELECT at, action, detail FROM audit_log ORDER BY id"))

    # ------------------------------------------------------------------ measurements
    def add_spectrum(self, spectrum: Spectrum, name: str) -> bool:
        """Store a spectrum. False if one with the same channel data is already stored
        (it is not stored twice; the first record is kept)."""
        if not spectrum.verify_integrity():
            raise ProjectStoreError("the spectrum does not match its own hash; not stored")
        if self.has_spectrum(spectrum.data_sha256):
            return False
        variance = _pack(spectrum.variance) if spectrum.variance is not None else None
        with self._db:
            self._db.execute(
                "INSERT INTO measurement (data_sha256, origin, kind, name, n_channels, record, "
                "counts, variance, added) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spectrum.data_sha256,
                    spectrum.origin.value,
                    spectrum.kind.value,
                    name,
                    spectrum.n_channels,
                    _json(_spectrum_record(spectrum)),
                    _pack(spectrum.counts),
                    variance,
                    _now(),
                ),
            )
            self._db.execute(
                "INSERT INTO audit_log (at, action, detail) VALUES (?, ?, ?)",
                (
                    _now(),
                    "add-spectrum",
                    f"{name} ({spectrum.origin.value}, {spectrum.data_sha256[:12]}…)",
                ),
            )
        return True

    def has_spectrum(self, data_sha256: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM measurement WHERE data_sha256 = ?", (data_sha256,)
        ).fetchone()
        return row is not None

    def spectrum_names(self) -> list[tuple[str, str, str, str]]:
        """(data hash, name, origin, kind) of every stored spectrum, in insertion order."""
        return list(
            self._db.execute("SELECT data_sha256, name, origin, kind FROM measurement ORDER BY id")
        )

    def spectrum(self, data_sha256: str) -> Spectrum:
        row = self._db.execute(
            "SELECT record, counts, variance FROM measurement WHERE data_sha256 = ?",
            (data_sha256,),
        ).fetchone()
        if row is None:
            raise ProjectStoreError(f"no stored spectrum {data_sha256[:12]}…")
        record, counts, variance = row
        return _spectrum_from(
            _unjson(record),
            _unpack(counts),
            _unpack(variance) if variance is not None else None,
            data_sha256,
        )

    def spectra(self) -> Iterator[tuple[str, Spectrum]]:
        for sha, name, _, _ in self.spectrum_names():
            yield name, self.spectrum(sha)

    # ------------------------------------------------------------------ backgrounds
    def set_background(self, data_sha256: str, designated: bool, note: str = "") -> None:
        if not self.has_spectrum(data_sha256):
            raise ProjectStoreError("store the spectrum before designating it")
        with self._db:
            self._db.execute(
                "INSERT INTO background_event (data_sha256, action, note, at) VALUES (?,?,?,?)",
                (data_sha256, "designate" if designated else "remove", note, _now()),
            )

    def backgrounds(self) -> dict[str, str]:
        """Hash -> note of every measurement currently designated as background."""
        state: dict[str, str] = {}
        for sha, action, note in self._db.execute(
            "SELECT data_sha256, action, note FROM background_event ORDER BY id"
        ):
            if action == "designate":
                state[sha] = note
            else:
                state.pop(sha, None)
        return state

    # ------------------------------------------------------------------ calibrations
    def add_calibration(
        self,
        kind: str,
        record: Mapping[str, Any],
        *,
        data_sha256: str | None,
        status: str,
        synthetic: bool,
        software: str,
        summary: Mapping[str, Any] | None = None,
    ) -> int:
        with self._db:
            cur = self._db.execute(
                "INSERT INTO calibration (kind, data_sha256, status, synthetic, record, summary, "
                "software, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    data_sha256,
                    status,
                    int(synthetic),
                    _json(record),
                    _json(summary or {}),
                    software,
                    _now(),
                ),
            )
        return int(cur.lastrowid or 0)

    def calibrations(
        self, kind: str | None = None, data_sha256: str | None = None
    ) -> list[StoredCalibration]:
        query = "SELECT id, kind, data_sha256, status, synthetic, record, summary, software, "
        query += "created FROM calibration WHERE 1 = 1"
        args: list[Any] = []
        if kind is not None:
            query += " AND kind = ?"
            args.append(kind)
        if data_sha256 is not None:
            query += " AND data_sha256 = ?"
            args.append(data_sha256)
        return [
            StoredCalibration(
                r[0], r[1], r[2], r[3], bool(r[4]), _unjson(r[5]), _unjson(r[6]), r[7], r[8]
            )
            for r in self._db.execute(query + " ORDER BY id", args)
        ]

    # ------------------------------------------------------------------ analyses
    def add_run(
        self,
        kind: str,
        *,
        data_sha256: str | None,
        synthetic: bool,
        label: str,
        settings: Mapping[str, Any],
        result: Mapping[str, Any],
        software: str,
    ) -> int:
        if data_sha256 is not None and not self.has_spectrum(data_sha256):
            raise ProjectStoreError("store the spectrum before results obtained from it")
        with self._db:
            cur = self._db.execute(
                "INSERT INTO analysis_run (kind, data_sha256, synthetic, label, settings, "
                "result, software, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    data_sha256,
                    int(synthetic),
                    label,
                    _json(settings),
                    _json(result),
                    software,
                    _now(),
                ),
            )
        return int(cur.lastrowid or 0)

    def runs(self, kind: str | None = None, data_sha256: str | None = None) -> list[StoredRun]:
        query = "SELECT id, kind, data_sha256, synthetic, label, settings, result, software, "
        query += "created FROM analysis_run WHERE 1 = 1"
        args: list[Any] = []
        if kind is not None:
            query += " AND kind = ?"
            args.append(kind)
        if data_sha256 is not None:
            query += " AND data_sha256 = ?"
            args.append(data_sha256)
        return [
            StoredRun(r[0], r[1], r[2], bool(r[3]), r[4], _unjson(r[5]), _unjson(r[6]), r[7], r[8])
            for r in self._db.execute(query + " ORDER BY id", args)
        ]

    # ------------------------------------------------------------------ reference data
    def add_reference_dataset(
        self,
        name: str,
        evaluations: Sequence[str],
        fictitious: bool,
        document: Mapping[str, Any],
        document_sha256: str,
    ) -> int:
        with self._db:
            cur = self._db.execute(
                "INSERT INTO reference_dataset (name, evaluations, fictitious, document, "
                "document_sha256, imported) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    name,
                    _json(list(evaluations)),
                    int(fictitious),
                    _json(document),
                    document_sha256,
                    _now(),
                ),
            )
        return int(cur.lastrowid or 0)

    def reference_datasets(self) -> list[tuple[int, str, list[str], bool, dict[str, Any], str]]:
        return [
            (r[0], r[1], _unjson(r[2]), bool(r[3]), _unjson(r[4]), r[5])
            for r in self._db.execute(
                "SELECT id, name, evaluations, fictitious, document, document_sha256 "
                "FROM reference_dataset ORDER BY id"
            )
        ]
