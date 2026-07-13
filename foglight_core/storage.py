"""Crash-safe local SQLite storage for canonical observations and incidents."""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .models import Incident, Observation, normalize_timestamp, observation_id

SCHEMA_VERSION = 6

MIGRATION_1 = """
CREATE TABLE providers (
    provider_id TEXT PRIMARY KEY,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE observations (
    observation_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(provider_id),
    provider_record_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    event_at TEXT,
    ingested_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    min_lon REAL,
    min_lat REAL,
    max_lon REAL,
    max_lat REAL,
    document_json TEXT NOT NULL,
    UNIQUE(provider_id, provider_record_id)
);
CREATE INDEX observations_ingested_idx ON observations(ingested_at);
CREATE INDEX observations_kind_event_idx ON observations(kind, event_at);
CREATE TABLE incidents (
    incident_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    priority_score INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_changed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    document_json TEXT NOT NULL
);
CREATE INDEX incidents_priority_idx ON incidents(priority_score DESC, last_changed_at DESC);
CREATE TABLE incident_observations (
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL REFERENCES observations(observation_id) ON DELETE CASCADE,
    PRIMARY KEY(incident_id, observation_id)
);
CREATE TABLE relations (
    source_incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    target_incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    PRIMARY KEY(source_incident_id, relation_type, target_incident_id)
);
CREATE TABLE revisions (
    incident_id TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    changed_at TEXT NOT NULL,
    change_type TEXT NOT NULL,
    document_json TEXT NOT NULL,
    PRIMARY KEY(incident_id, revision)
);
CREATE TABLE source_health (
    provider_id TEXT PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    latency_ms REAL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE observation_spatial (
    observation_id TEXT PRIMARY KEY REFERENCES observations(observation_id) ON DELETE CASCADE,
    min_lon REAL NOT NULL,
    max_lon REAL NOT NULL,
    min_lat REAL NOT NULL,
    max_lat REAL NOT NULL
);
CREATE INDEX observation_spatial_bbox_idx
    ON observation_spatial(min_lon, max_lon, min_lat, max_lat);
"""

MIGRATION_2 = """
CREATE TABLE scheduler_state (
    provider_id TEXT PRIMARY KEY REFERENCES providers(provider_id) ON DELETE CASCADE,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX revisions_changed_idx ON revisions(changed_at, incident_id, revision);
"""

MIGRATION_3 = """
ALTER TABLE incidents ADD COLUMN lane TEXT NOT NULL DEFAULT 'optional_signal';
ALTER TABLE incidents ADD COLUMN min_lon REAL;
ALTER TABLE incidents ADD COLUMN min_lat REAL;
ALTER TABLE incidents ADD COLUMN max_lon REAL;
ALTER TABLE incidents ADD COLUMN max_lat REAL;
CREATE INDEX incidents_lane_priority_idx
    ON incidents(lane, priority_score DESC, last_changed_at DESC);
CREATE INDEX incidents_kind_priority_idx
    ON incidents(kind, priority_score DESC, last_changed_at DESC);
"""

MIGRATION_4 = """
CREATE TABLE change_log (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    FOREIGN KEY(incident_id, revision)
        REFERENCES revisions(incident_id, revision) ON DELETE CASCADE,
    UNIQUE(incident_id, revision)
);
INSERT INTO change_log(incident_id, revision)
    SELECT incident_id, revision FROM revisions ORDER BY changed_at, incident_id, revision;
"""

MIGRATION_5 = """
CREATE VIRTUAL TABLE incident_search USING fts5(
    incident_id UNINDEXED,
    headline,
    summary,
    location_name,
    kind,
    status,
    tokenize='unicode61 remove_diacritics 2'
);
INSERT INTO incident_search(
    rowid, incident_id, headline, summary, location_name, kind, status
)
SELECT
    rowid,
    incident_id,
    COALESCE(CASE WHEN json_valid(document_json) THEN json_extract(document_json, '$.headline') END, ''),
    COALESCE(CASE WHEN json_valid(document_json) THEN json_extract(document_json, '$.summary') END, ''),
    COALESCE((
        SELECT CASE WHEN json_valid(o.document_json)
            THEN json_extract(o.document_json, '$.location_name') END
        FROM incident_observations io JOIN observations o USING(observation_id)
        WHERE io.incident_id=incidents.incident_id
          AND CASE WHEN json_valid(o.document_json)
              THEN COALESCE(json_extract(o.document_json, '$.location_name'), '') END <> ''
        ORDER BY o.ingested_at DESC, o.observation_id LIMIT 1
    ), ''),
    kind,
    status
FROM incidents;
CREATE TRIGGER incidents_search_insert AFTER INSERT ON incidents BEGIN
    INSERT INTO incident_search(
        rowid, incident_id, headline, summary, location_name, kind, status
    ) VALUES (
        new.rowid,
        new.incident_id,
        COALESCE(CASE WHEN json_valid(new.document_json) THEN json_extract(new.document_json, '$.headline') END, ''),
        COALESCE(CASE WHEN json_valid(new.document_json) THEN json_extract(new.document_json, '$.summary') END, ''),
        COALESCE(CASE WHEN json_valid(new.document_json) THEN json_extract(new.document_json, '$.location_name') END, ''),
        new.kind,
        new.status
    );
END;
CREATE TRIGGER incidents_search_update AFTER UPDATE ON incidents BEGIN
    DELETE FROM incident_search WHERE rowid=old.rowid;
    INSERT INTO incident_search(
        rowid, incident_id, headline, summary, location_name, kind, status
    ) VALUES (
        new.rowid,
        new.incident_id,
        COALESCE(CASE WHEN json_valid(new.document_json) THEN json_extract(new.document_json, '$.headline') END, ''),
        COALESCE(CASE WHEN json_valid(new.document_json) THEN json_extract(new.document_json, '$.summary') END, ''),
        COALESCE(CASE WHEN json_valid(new.document_json) THEN json_extract(new.document_json, '$.location_name') END, ''),
        new.kind,
        new.status
    );
END;
CREATE TRIGGER incidents_search_delete AFTER DELETE ON incidents BEGIN
    DELETE FROM incident_search WHERE rowid=old.rowid;
END;
"""

MIGRATION_6 = """
UPDATE incident_search
SET location_name=COALESCE((
    SELECT CASE WHEN json_valid(o.document_json)
        THEN json_extract(o.document_json, '$.location_name') END
    FROM incidents i
    JOIN incident_observations io ON io.incident_id=i.incident_id
    JOIN observations o USING(observation_id)
    WHERE i.rowid=incident_search.rowid
      AND CASE WHEN json_valid(o.document_json)
          THEN COALESCE(json_extract(o.document_json, '$.location_name'), '') END <> ''
    ORDER BY o.ingested_at DESC, o.observation_id LIMIT 1
), '');
CREATE TRIGGER incident_observations_search_insert
AFTER INSERT ON incident_observations BEGIN
    UPDATE incident_search SET location_name=COALESCE((
        SELECT CASE WHEN json_valid(o.document_json)
            THEN json_extract(o.document_json, '$.location_name') END
        FROM incident_observations io JOIN observations o USING(observation_id)
        WHERE io.incident_id=new.incident_id
          AND CASE WHEN json_valid(o.document_json)
              THEN COALESCE(json_extract(o.document_json, '$.location_name'), '') END <> ''
        ORDER BY o.ingested_at DESC, o.observation_id LIMIT 1
    ), '') WHERE rowid=(SELECT rowid FROM incidents WHERE incident_id=new.incident_id);
END;
CREATE TRIGGER incident_observations_search_delete
AFTER DELETE ON incident_observations BEGIN
    UPDATE incident_search SET location_name=COALESCE((
        SELECT CASE WHEN json_valid(o.document_json)
            THEN json_extract(o.document_json, '$.location_name') END
        FROM incident_observations io JOIN observations o USING(observation_id)
        WHERE io.incident_id=old.incident_id
          AND CASE WHEN json_valid(o.document_json)
              THEN COALESCE(json_extract(o.document_json, '$.location_name'), '') END <> ''
        ORDER BY o.ingested_at DESC, o.observation_id LIMIT 1
    ), '') WHERE rowid=(SELECT rowid FROM incidents WHERE incident_id=old.incident_id);
END;
CREATE TRIGGER observations_search_update
AFTER UPDATE OF document_json ON observations BEGIN
    UPDATE incident_search SET location_name=COALESCE((
        SELECT CASE WHEN json_valid(o.document_json)
            THEN json_extract(o.document_json, '$.location_name') END
        FROM incident_observations members JOIN observations o USING(observation_id)
        WHERE members.incident_id=(
            SELECT incident_id FROM incidents WHERE rowid=incident_search.rowid
        )
          AND CASE WHEN json_valid(o.document_json)
              THEN COALESCE(json_extract(o.document_json, '$.location_name'), '') END <> ''
        ORDER BY o.ingested_at DESC, o.observation_id LIMIT 1
    ), '') WHERE rowid IN (
        SELECT i.rowid FROM incident_observations io
        JOIN incidents i USING(incident_id) WHERE io.observation_id=new.observation_id
    );
END;
"""

MIGRATIONS = {
    1: MIGRATION_1, 2: MIGRATION_2, 3: MIGRATION_3, 4: MIGRATION_4,
    5: MIGRATION_5, 6: MIGRATION_6,
}


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionReport:
    dry_run: bool
    expired_observations: int
    overflow_observations: int
    size_cap_observations: int
    deleted_observations: int
    before_bytes: int
    after_bytes: int
    max_bytes: int
    size_cap_satisfied: bool


class CorruptDatabaseError(sqlite3.DatabaseError):
    pass


class ObservationStore:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = 5000,
        max_observations: int = 100_000,
        max_bytes: int = 256 * 1024 * 1024,
        recover_corruption: bool = True,
    ) -> None:
        if not 100 <= busy_timeout_ms <= 60_000:
            raise ValueError("busy_timeout_ms must be in 100..60000")
        if max_observations < 1 or max_bytes < 1024 * 1024:
            raise ValueError("storage caps are too small")
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.max_observations = max_observations
        self.max_bytes = max_bytes
        self.recover_corruption = recover_corruption
        self._write_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rtree_enabled = False
        self.last_quarantine: Path | None = None
        self._initialize_with_recovery()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            connection.close()
            raise
        return connection

    def _initialize_with_recovery(self) -> None:
        try:
            self._initialize()
        except CorruptDatabaseError:
            if not self.recover_corruption or not self.path.exists():
                raise
            self.last_quarantine = self._quarantine()
            self._initialize()

    def _initialize(self) -> None:
        connection = None
        try:
            connection = self._connect()
            result = connection.execute("PRAGMA quick_check").fetchone()[0]
        except sqlite3.DatabaseError as error:
            if connection is not None:
                connection.close()
            raise CorruptDatabaseError(str(error)) from error
        try:
            if result != "ok":
                raise CorruptDatabaseError(f"quick_check failed: {result}")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version, script in sorted(MIGRATIONS.items()):
                if version in applied:
                    continue
                self._apply_migration(connection, version, script)
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
            if current != SCHEMA_VERSION:
                raise sqlite3.DatabaseError(f"unexpected schema version {current}")
            self.rtree_enabled = self._initialize_rtree(connection)
        finally:
            connection.close()

    @staticmethod
    def _apply_migration(connection, version, script) -> None:
        applied_at = normalize_timestamp(dt.datetime.now(dt.UTC), required=True)
        connection.execute("BEGIN IMMEDIATE")
        try:
            statement = ""
            for character in script:
                statement += character
                if character == ";" and sqlite3.complete_statement(statement):
                    connection.execute(statement)
                    statement = ""
            if statement.strip():
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, applied_at),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _initialize_rtree(connection) -> bool:
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS observation_rtree USING rtree("
                "rowid, min_lon, max_lon, min_lat, max_lat)"
            )
            # A previous process may have used the portable fallback when R*Tree
            # was unavailable. Rebuild also removes entries orphaned by a crash or
            # an older Foglight release.
            connection.execute("DELETE FROM observation_rtree")
            connection.execute(
                "INSERT INTO observation_rtree "
                "SELECT o.rowid, s.min_lon, s.max_lon, s.min_lat, s.max_lat "
                "FROM observation_spatial s JOIN observations o USING(observation_id)"
            )
            return True
        except sqlite3.OperationalError:
            return False

    def _quarantine(self) -> Path:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        counter = 1
        while target.exists():
            target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{counter}")
            counter += 1
        shutil.move(self.path, target)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        return target

    @contextlib.contextmanager
    def transaction(self, *, immediate=False):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def register_provider(self, provider_id: str, metadata: dict) -> None:
        if not isinstance(metadata, dict):
            raise TypeError("provider metadata must be an object")
        observation_id(provider_id, "provider-validation")
        updated_at = normalize_timestamp(dt.datetime.now(dt.UTC), required=True)
        document = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(document.encode("utf-8")) > 64 * 1024:
            raise ValueError("provider metadata exceeds 64 KiB")
        with self._write_lock, self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO providers(provider_id, metadata_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(provider_id) DO UPDATE SET metadata_json=excluded.metadata_json, "
                "updated_at=excluded.updated_at",
                (provider_id, document, updated_at),
            )

    def upsert_observation(self, observation: Observation) -> bool:
        if not isinstance(observation, Observation):
            raise TypeError("observation must be canonical")
        if observation.bbox:
            min_lon, min_lat, max_lon, max_lat = observation.bbox
        else:
            min_lon = min_lat = max_lon = max_lat = None
        document = json.dumps(observation.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._write_lock, self.transaction(immediate=True) as connection:
            previous = connection.execute(
                "SELECT content_hash FROM observations WHERE observation_id=?",
                (observation.observation_id,),
            ).fetchone()
            changed = previous is None or previous[0] != observation.content_hash
            connection.execute(
                "INSERT INTO observations(observation_id, provider_id, provider_record_id, kind, "
                "status, event_at, ingested_at, content_hash, min_lon, min_lat, max_lon, max_lat, "
                "document_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(observation_id) DO UPDATE SET kind=excluded.kind, "
                "status=excluded.status, event_at=excluded.event_at, "
                "ingested_at=excluded.ingested_at, "
                "content_hash=excluded.content_hash, min_lon=excluded.min_lon, "
                "min_lat=excluded.min_lat, max_lon=excluded.max_lon, max_lat=excluded.max_lat, "
                "document_json=excluded.document_json",
                (
                    observation.observation_id, observation.provider_id,
                    observation.provider_record_id, observation.kind.value,
                    observation.status.value, observation.event_at, observation.ingested_at,
                    observation.content_hash, min_lon, min_lat, max_lon, max_lat, document,
                ),
            )
            connection.execute(
                "DELETE FROM observation_spatial WHERE observation_id=?",
                (observation.observation_id,),
            )
            rowid = connection.execute(
                "SELECT rowid FROM observations WHERE observation_id=?",
                (observation.observation_id,),
            ).fetchone()[0]
            if self.rtree_enabled:
                connection.execute("DELETE FROM observation_rtree WHERE rowid=?", (rowid,))
            if observation.bbox:
                connection.execute(
                    "INSERT INTO observation_spatial VALUES (?, ?, ?, ?, ?)",
                    (observation.observation_id, min_lon, max_lon, min_lat, max_lat),
                )
                if self.rtree_enabled:
                    connection.execute(
                        "INSERT INTO observation_rtree VALUES (?, ?, ?, ?, ?)",
                        (rowid, min_lon, max_lon, min_lat, max_lat),
                    )
        return changed

    def get_observation(self, observation_id: str) -> Observation | None:
        with contextlib.closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT document_json FROM observations WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
        return Observation.from_dict(json.loads(row[0])) if row else None

    def observations_by_ids(self, observation_ids) -> dict[str, Observation]:
        """Load arbitrary incident evidence without SQLite variable-limit surprises."""
        identifiers = tuple(dict.fromkeys(str(item) for item in observation_ids))
        output = {}
        with contextlib.closing(self._connect()) as connection:
            for start in range(0, len(identifiers), 500):
                batch = identifiers[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT observation_id, document_json FROM observations "
                    f"WHERE observation_id IN ({placeholders})",
                    batch,
                ).fetchall()
                output.update(
                    (row[0], Observation.from_dict(json.loads(row[1]))) for row in rows
                )
        return output

    def observations_for_provider(self, provider_id: str, *, limit=1000) -> list[Observation]:
        limit = max(1, min(10_000, int(limit)))
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM observations WHERE provider_id=? "
                "ORDER BY ingested_at DESC, observation_id LIMIT ?",
                (provider_id, limit),
            ).fetchall()
        return [Observation.from_dict(json.loads(row[0])) for row in rows]

    def query_bbox(self, west, south, east, north, *, limit=1000) -> list[Observation]:
        observation_ids = self.query_bbox_ids(west, south, east, north, limit=limit)
        if not observation_ids:
            return []
        placeholders = ",".join("?" for _ in observation_ids)
        with contextlib.closing(self._connect()) as connection:
            documents = {
                row[0]: row[1]
                for row in connection.execute(
                    f"SELECT observation_id, document_json FROM observations "
                    f"WHERE observation_id IN ({placeholders})",
                    observation_ids,
                )
            }
        return [Observation.from_dict(json.loads(documents[item])) for item in observation_ids]

    def query_bbox_ids(self, west, south, east, north, *, limit=1000) -> list[str]:
        values = tuple(float(item) for item in (west, south, east, north))
        west, south, east, north = values
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError("invalid bounding box")
        limit = max(1, min(10_000, int(limit)))
        with contextlib.closing(self._connect()) as connection:
            if self.rtree_enabled:
                rows = connection.execute(
                    "SELECT o.observation_id FROM observation_rtree r "
                    "JOIN observations o ON o.rowid=r.rowid "
                    "WHERE r.max_lon>=? AND r.min_lon<=? AND r.max_lat>=? AND r.min_lat<=? "
                    "ORDER BY o.ingested_at DESC LIMIT ?",
                    (west, east, south, north, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT o.observation_id FROM observation_spatial s "
                    "JOIN observations o USING(observation_id) "
                    "WHERE s.max_lon>=? AND s.min_lon<=? AND s.max_lat>=? AND s.min_lat<=? "
                    "ORDER BY o.ingested_at DESC LIMIT ?",
                    (west, east, south, north, limit),
                ).fetchall()
        return [row[0] for row in rows]

    def upsert_incident(self, incident: Incident) -> None:
        if not isinstance(incident, Incident):
            raise TypeError("incident must be canonical")
        document = json.dumps(incident.to_dict(), sort_keys=True, separators=(",", ":"))
        if incident.bbox:
            min_lon, min_lat, max_lon, max_lat = incident.bbox
        else:
            min_lon = min_lat = max_lon = max_lat = None
        lane = str(incident.priority_components.get("lane") or "optional_signal")
        with self._write_lock, self.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT revision FROM incidents WHERE incident_id=?",
                (incident.incident_id,),
            ).fetchone()
            if current and incident.revision < current[0]:
                raise ValueError("incident revision cannot move backwards")
            existing_revision = connection.execute(
                "SELECT document_json FROM revisions WHERE incident_id=? AND revision=?",
                (incident.incident_id, incident.revision),
            ).fetchone()
            if existing_revision and existing_revision[0] != document:
                raise ValueError("incident revision already exists with different content")
            connection.execute(
                "INSERT INTO incidents(incident_id, kind, status, priority_score, first_seen_at, "
                "last_changed_at, last_observed_at, revision, document_json, lane, min_lon, "
                "min_lat, max_lon, max_lat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(incident_id) DO UPDATE SET kind=excluded.kind, status=excluded.status, "
                "priority_score=excluded.priority_score, first_seen_at=excluded.first_seen_at, "
                "last_changed_at=excluded.last_changed_at, "
                "last_observed_at=excluded.last_observed_at, revision=excluded.revision, "
                "document_json=excluded.document_json, lane=excluded.lane, "
                "min_lon=excluded.min_lon, min_lat=excluded.min_lat, "
                "max_lon=excluded.max_lon, max_lat=excluded.max_lat",
                (
                    incident.incident_id, incident.kind.value, incident.status.value,
                    incident.priority_score, incident.first_seen_at, incident.last_changed_at,
                    incident.last_observed_at, incident.revision, document, lane,
                    min_lon, min_lat, max_lon, max_lat,
                ),
            )
            connection.execute(
                "DELETE FROM incident_observations WHERE incident_id=?", (incident.incident_id,)
            )
            connection.executemany(
                "INSERT INTO incident_observations VALUES (?, ?)",
                [(incident.incident_id, item) for item in incident.observation_ids],
            )
            connection.execute(
                "UPDATE incident_search SET location_name=COALESCE(("
                "SELECT CASE WHEN json_valid(o.document_json) "
                "THEN json_extract(o.document_json, '$.location_name') END "
                "FROM incident_observations io JOIN observations o USING(observation_id) "
                "WHERE io.incident_id=? AND CASE WHEN json_valid(o.document_json) "
                "THEN COALESCE(json_extract(o.document_json, '$.location_name'), '') "
                "END <> '' ORDER BY o.ingested_at DESC, o.observation_id LIMIT 1"
                "), '') WHERE rowid=(SELECT rowid FROM incidents WHERE incident_id=?)",
                (incident.incident_id, incident.incident_id),
            )
            connection.execute(
                "DELETE FROM relations WHERE source_incident_id=?", (incident.incident_id,)
            )
            connection.executemany(
                "INSERT INTO relations VALUES (?, ?, ?)",
                [
                    (incident.incident_id, relation.relation_type.value, relation.target_incident_id)
                    for relation in incident.relations
                ],
            )
            revision_result = connection.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(incident_id, revision) DO NOTHING",
                (
                    incident.incident_id, incident.revision, incident.last_changed_at,
                    incident.change_type.value, document,
                ),
            )
            if revision_result.rowcount:
                connection.execute(
                    "INSERT INTO change_log(incident_id, revision) VALUES (?, ?)",
                    (incident.incident_id, incident.revision),
                )

    def get_incident(self, incident_id: str) -> Incident | None:
        with contextlib.closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT document_json FROM incidents WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
        return Incident.from_dict(json.loads(row[0])) if row else None

    def incident_for_observation(self, observation_id: str) -> Incident | None:
        with contextlib.closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT i.document_json FROM incident_observations io "
                "JOIN incidents i USING(incident_id) WHERE io.observation_id=? "
                "ORDER BY i.revision DESC, i.incident_id LIMIT 1",
                (observation_id,),
            ).fetchone()
        return Incident.from_dict(json.loads(row[0])) if row else None

    def incidents_for_provider(self, provider_id: str) -> list[Incident]:
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT DISTINCT i.document_json FROM incidents i "
                "JOIN incident_observations io USING(incident_id) "
                "JOIN observations o USING(observation_id) WHERE o.provider_id=? "
                "ORDER BY i.incident_id",
                (provider_id,),
            ).fetchall()
        return [Incident.from_dict(json.loads(row[0])) for row in rows]

    def observations_for_incidents(
        self, incident_ids: list[str], *, limit_per_incident=200
    ) -> dict[str, list[Observation]]:
        if not incident_ids:
            return {}
        limit_per_incident = max(1, min(1000, int(limit_per_incident)))
        placeholders = ",".join("?" for _ in incident_ids)
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT incident_id, document_json FROM ("
                "SELECT io.incident_id, o.document_json, ROW_NUMBER() OVER ("
                "PARTITION BY io.incident_id ORDER BY o.observation_id) AS member_number "
                "FROM incident_observations io JOIN observations o USING(observation_id) "
                f"WHERE io.incident_id IN ({placeholders})) WHERE member_number<=? "
                "ORDER BY incident_id, member_number",
                (*incident_ids, limit_per_incident),
            ).fetchall()
        output = {incident_id: [] for incident_id in incident_ids}
        for row in rows:
            output[row[0]].append(Observation.from_dict(json.loads(row[1])))
        return output

    def list_incidents(self, *, limit=100) -> list[Incident]:
        limit = max(1, min(1000, int(limit)))
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM incidents "
                "ORDER BY priority_score DESC, last_changed_at DESC, incident_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [Incident.from_dict(json.loads(row[0])) for row in rows]

    def correlation_incidents(
        self,
        *,
        include_kinds: set[str] | frozenset[str] | None = None,
        exclude_kinds: set[str] | frozenset[str] | None = None,
    ) -> list[Incident]:
        """Return the complete deterministic candidate set for correlation.

        This deliberately has no presentation-page limit. The caller narrows by
        kind, and the storage retention cap provides the absolute upper bound.
        """
        if include_kinds is not None and exclude_kinds is not None:
            raise ValueError("include_kinds and exclude_kinds are mutually exclusive")
        clauses, params = [], []
        selected = include_kinds if include_kinds is not None else exclude_kinds
        if selected is not None:
            values = sorted(str(item) for item in selected)
            if not values:
                return [] if include_kinds is not None else self.correlation_incidents()
            placeholders = ",".join("?" for _ in values)
            operator = "IN" if include_kinds is not None else "NOT IN"
            clauses.append(f"kind {operator} ({placeholders})")
            params.extend(values)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT document_json FROM incidents{where} ORDER BY incident_id",
                params,
            ).fetchall()
        return [Incident.from_dict(json.loads(row[0])) for row in rows]

    def query_incidents(
        self,
        *,
        limit=100,
        offset=0,
        kind: str | None = None,
        lane: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> tuple[list[Incident], int]:
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        if lane is not None:
            clauses.append("lane=?")
            params.append(lane)
        if bbox is not None:
            west, south, east, north = bbox
            clauses.append(
                "max_lon IS NOT NULL AND max_lon>=? AND min_lon<=? "
                "AND max_lat>=? AND min_lat<=?"
            )
            params.extend((west, east, south, north))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with contextlib.closing(self._connect()) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM incidents{where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT document_json FROM incidents{where} "
                "ORDER BY priority_score DESC, last_changed_at DESC, incident_id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [Incident.from_dict(json.loads(row[0])) for row in rows], total

    def search_incidents(self, query: str, *, limit=50) -> list[Incident]:
        """Search bounded retained incident fields without exposing raw observations."""
        terms = re.findall(r"[^\W_]+", str(query).casefold(), flags=re.UNICODE)[:8]
        if not terms:
            return []
        limit = max(1, min(200, int(limit)))
        # Treat every user term as a quoted prefix token. Quoting prevents FTS
        # operators in user input from changing the query while the suffix
        # keeps short, partial place and category searches useful.
        match = " AND ".join(f'"{term}"*' for term in terms)
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT i.document_json FROM incident_search s "
                "JOIN incidents i ON i.rowid=s.rowid WHERE incident_search MATCH ? "
                "ORDER BY i.priority_score DESC, i.last_changed_at DESC, i.incident_id LIMIT ?",
                (match, limit),
            ).fetchall()
        return [Incident.from_dict(json.loads(row[0])) for row in rows]

    def timeline(self, incident_id: str, *, limit=100) -> list[dict]:
        limit = max(1, min(1000, int(limit)))
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT c.cursor, r.revision, r.changed_at, r.change_type, r.document_json "
                "FROM revisions r JOIN change_log c USING(incident_id, revision) "
                "WHERE r.incident_id=? ORDER BY r.revision DESC LIMIT ?",
                (incident_id, limit),
            ).fetchall()
        return [
            {
                "cursor": row[0],
                "revision": row[1],
                "changed_at": row[2],
                "change_type": row[3],
                "incident": json.loads(row[4]),
            }
            for row in rows
        ]

    def changes_after(self, cursor=0, *, limit=100) -> list[dict]:
        cursor = max(0, int(cursor))
        limit = max(1, min(1000, int(limit)))
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT c.cursor, r.incident_id, r.revision, r.changed_at, r.change_type, "
                "r.document_json FROM change_log c JOIN revisions r USING(incident_id, revision) "
                "WHERE c.cursor>? ORDER BY c.cursor LIMIT ?",
                (cursor, limit),
            ).fetchall()
        return [
            {
                "cursor": row[0],
                "incident_id": row[1],
                "revision": row[2],
                "changed_at": row[3],
                "change_type": row[4],
                "incident": json.loads(row[5]),
            }
            for row in rows
        ]

    def latest_change_cursor(self) -> int:
        with contextlib.closing(self._connect()) as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(MAX(cursor), 0) FROM change_log"
                ).fetchone()[0]
            )

    def latest_revision_metadata(self) -> dict[str, int | str | None]:
        with contextlib.closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT c.cursor, r.changed_at FROM change_log c "
                "JOIN revisions r USING(incident_id, revision) "
                "ORDER BY c.cursor DESC LIMIT 1"
            ).fetchone()
        return {
            "revision_cursor": int(row[0]) if row else 0,
            "last_revision_at": row[1] if row else None,
        }

    def source_health(self, provider_id: str | None = None) -> list[dict]:
        query = (
            "SELECT provider_id, status, checked_at, latency_ms, detail FROM source_health"
        )
        params = ()
        if provider_id is not None:
            query += " WHERE provider_id=?"
            params = (provider_id,)
        query += " ORDER BY provider_id"
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def save_scheduler_state(self, provider_id: str, state: dict) -> None:
        document = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)
        updated_at = normalize_timestamp(dt.datetime.now(dt.UTC), required=True)
        with self._write_lock, self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO scheduler_state VALUES (?, ?, ?) "
                "ON CONFLICT(provider_id) DO UPDATE SET state_json=excluded.state_json, "
                "updated_at=excluded.updated_at",
                (provider_id, document, updated_at),
            )

    def load_scheduler_state(self, provider_id: str) -> dict | None:
        with contextlib.closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT state_json FROM scheduler_state WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    def scheduler_states(self, provider_id: str | None = None) -> dict[str, dict]:
        query = "SELECT provider_id, state_json FROM scheduler_state"
        params = ()
        if provider_id is not None:
            query += " WHERE provider_id=?"
            params = (provider_id,)
        with contextlib.closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        output = {}
        for row in rows:
            try:
                value = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                output[row[0]] = value
        return output

    def update_source_health(
        self, provider_id, status, checked_at, *, latency_ms=None, detail=""
    ) -> None:
        checked = normalize_timestamp(checked_at, required=True)
        if status not in {"live", "cached", "stale", "error", "idle"}:
            raise ValueError("invalid source health status")
        if latency_ms is not None:
            if isinstance(latency_ms, bool) or not isinstance(latency_ms, (int, float)):
                raise TypeError("latency must be numeric")
            if not math.isfinite(latency_ms) or latency_ms < 0:
                raise ValueError("latency must be finite and non-negative")
        with self._write_lock, self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO source_health VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_id) DO UPDATE SET status=excluded.status, "
                "checked_at=excluded.checked_at, latency_ms=excluded.latency_ms, detail=excluded.detail",
                (provider_id, status, checked, latency_ms, str(detail)[:1000]),
            )

    @staticmethod
    def _verify_database(connection: sqlite3.Connection) -> None:
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise CorruptDatabaseError(f"integrity_check failed: {integrity}")
            version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
            if version != SCHEMA_VERSION:
                raise CorruptDatabaseError(f"unexpected backup schema version {version}")
            violation = connection.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise CorruptDatabaseError("backup contains foreign-key violations")
        except sqlite3.DatabaseError as error:
            if isinstance(error, CorruptDatabaseError):
                raise
            raise CorruptDatabaseError(str(error)) from error

    def backup(self, destination: str | os.PathLike[str]) -> Path:
        """Create and verify a transactionally consistent SQLite backup."""
        target = Path(destination)
        if target.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        with self._write_lock:
            try:
                if temporary.exists():
                    temporary.unlink()
                with (
                    contextlib.closing(self._connect()) as source,
                    contextlib.closing(sqlite3.connect(temporary)) as output,
                ):
                    source.backup(output)
                    self._verify_database(output)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return target

    def restore_from_backup(
        self,
        source: str | os.PathLike[str],
        *,
        safety_backup: str | os.PathLike[str] | None = None,
    ) -> Path:
        """Restore a verified backup and return the pre-restore safety copy."""
        backup_path = Path(source)
        if backup_path.resolve() == self.path.resolve():
            raise ValueError("restore source must differ from the live database")
        if not backup_path.is_file():
            raise FileNotFoundError(backup_path)
        if safety_backup is None:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            safety_path = self.path.with_name(f"{self.path.name}.pre-restore-{stamp}")
            counter = 1
            while safety_path.exists():
                safety_path = self.path.with_name(
                    f"{self.path.name}.pre-restore-{stamp}-{counter}"
                )
                counter += 1
        else:
            safety_path = Path(safety_backup)
        if safety_path.resolve() in {self.path.resolve(), backup_path.resolve()}:
            raise ValueError("safety backup must be a separate file")

        with contextlib.closing(sqlite3.connect(backup_path)) as candidate:
            candidate.execute("PRAGMA query_only=ON")
            self._verify_database(candidate)

        with self._write_lock:
            self.backup(safety_path)
            try:
                with (
                    contextlib.closing(sqlite3.connect(backup_path)) as candidate,
                    contextlib.closing(self._connect()) as destination,
                ):
                    candidate.execute("PRAGMA query_only=ON")
                    candidate.backup(destination)
                    self._verify_database(destination)
                self._initialize()
            except Exception:
                with (
                    contextlib.closing(sqlite3.connect(safety_path)) as candidate,
                    contextlib.closing(self._connect()) as destination,
                ):
                    candidate.backup(destination)
                self._initialize()
                raise
        return safety_path

    def database_bytes(self) -> int:
        with contextlib.closing(self._connect()) as connection:
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        return page_count * page_size

    def _vacuum(self) -> None:
        """Compact free pages outside a transaction so the size cap is measurable."""
        with contextlib.closing(self._connect()) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")

    def _delete_observations(self, connection, observation_ids: list[str]) -> None:
        if not observation_ids:
            return
        if self.rtree_enabled:
            placeholders = ",".join("?" for _ in observation_ids)
            rowids = [
                row[0]
                for row in connection.execute(
                    f"SELECT rowid FROM observations WHERE observation_id IN ({placeholders})",
                    observation_ids,
                )
            ]
            connection.executemany(
                "DELETE FROM observation_rtree WHERE rowid=?",
                [(item,) for item in rowids],
            )
        connection.executemany(
            "DELETE FROM observations WHERE observation_id=?",
            [(item,) for item in observation_ids],
        )
        # Incidents without evidence are no longer inspectable and otherwise
        # retain revisions indefinitely after their observations expire.
        orphan_ids = [
            row[0]
            for row in connection.execute(
                "SELECT incident_id FROM incidents WHERE NOT EXISTS ("
                "SELECT 1 FROM incident_observations io "
                "WHERE io.incident_id=incidents.incident_id)"
            )
        ]
        affected_sources = []
        if orphan_ids:
            placeholders = ",".join("?" for _ in orphan_ids)
            affected_sources = [
                row[0]
                for row in connection.execute(
                    f"SELECT DISTINCT source_incident_id FROM relations "
                    f"WHERE target_incident_id IN ({placeholders})",
                    orphan_ids,
                )
                if row[0] not in orphan_ids
            ]
        connection.execute(
            "DELETE FROM incidents WHERE NOT EXISTS ("
            "SELECT 1 FROM incident_observations io "
            "WHERE io.incident_id=incidents.incident_id)"
        )
        changed_at = normalize_timestamp(dt.datetime.now(dt.UTC), required=True)
        for source_id in affected_sources:
            row = connection.execute(
                "SELECT document_json FROM incidents WHERE incident_id=?", (source_id,)
            ).fetchone()
            if row is None:
                continue
            payload = json.loads(row[0])
            relations = payload.get("relations") or []
            filtered = [
                relation for relation in relations
                if relation.get("target_incident_id") not in orphan_ids
            ]
            if filtered == relations:
                continue
            payload["relations"] = filtered
            payload["revision"] = int(payload["revision"]) + 1
            payload["last_changed_at"] = max(payload["last_changed_at"], changed_at)
            payload["change_type"] = "updated"
            document = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE incidents SET last_changed_at=?, revision=?, document_json=? "
                "WHERE incident_id=?",
                (payload["last_changed_at"], payload["revision"], document, source_id),
            )
            connection.execute(
                "INSERT INTO revisions VALUES (?, ?, ?, ?, ?)",
                (
                    source_id, payload["revision"], payload["last_changed_at"],
                    payload["change_type"], document,
                ),
            )
            connection.execute(
                "INSERT INTO change_log(incident_id, revision) VALUES (?, ?)",
                (source_id, payload["revision"]),
            )

    def _delete_oldest(self, limit: int) -> list[str]:
        """Delete and return up to ``limit`` oldest observations atomically."""
        with self._write_lock, self.transaction(immediate=True) as connection:
            observation_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT observation_id FROM observations "
                    "ORDER BY ingested_at, observation_id LIMIT ?",
                    (limit,),
                )
            ]
            self._delete_observations(connection, observation_ids)
        return observation_ids

    def enforce_retention(self, *, retain_days: int, dry_run=False) -> RetentionReport:
        if retain_days < 1:
            raise ValueError("retain_days must be positive")
        cutoff = normalize_timestamp(dt.datetime.now(dt.UTC) - dt.timedelta(days=retain_days), required=True)
        before = self.database_bytes()
        with self._write_lock, self.transaction(immediate=True) as connection:
            expired_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT observation_id FROM observations WHERE ingested_at < ? ORDER BY ingested_at",
                    (cutoff,),
                )
            ]
            count = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            overflow = max(0, count - len(expired_ids) - self.max_observations)
            overflow_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT observation_id FROM observations WHERE observation_id NOT IN "
                    "(SELECT observation_id FROM observations WHERE ingested_at < ?) "
                    "ORDER BY ingested_at LIMIT ?",
                    (cutoff, overflow),
                )
            ] if overflow else []
            targets = expired_ids + overflow_ids
            if not dry_run and targets:
                self._delete_observations(connection, targets)
        size_cap_ids: list[str] = []
        if not dry_run:
            # Deletions only add free pages. Compact first, then evict the oldest
            # remaining observations in bounded batches until the logical database
            # fits. A schema-only database may have an irreducible minimum size.
            if targets or before > self.max_bytes:
                self._vacuum()
            while self.database_bytes() > self.max_bytes:
                current_bytes = self.database_bytes()
                with contextlib.closing(self._connect()) as connection:
                    remaining = connection.execute(
                        "SELECT COUNT(*) FROM observations"
                    ).fetchone()[0]
                if not remaining:
                    break
                average_bytes = max(512, current_bytes // remaining)
                needed = math.ceil((current_bytes - self.max_bytes) / average_bytes)
                deleted = self._delete_oldest(max(1, min(500, needed)))
                if not deleted:
                    break
                size_cap_ids.extend(deleted)
                self._vacuum()
        after = self.database_bytes()
        return RetentionReport(
            dry_run=dry_run,
            expired_observations=len(expired_ids),
            overflow_observations=len(overflow_ids),
            size_cap_observations=len(size_cap_ids),
            deleted_observations=0 if dry_run else len(targets) + len(size_cap_ids),
            before_bytes=before,
            after_bytes=after,
            max_bytes=self.max_bytes,
            size_cap_satisfied=after <= self.max_bytes,
        )
