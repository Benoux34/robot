from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
DEFAULT_DB = Path(".robot/runs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    dataset     TEXT,
    task_id     TEXT,
    source      TEXT NOT NULL,
    commit_sha  TEXT,
    status      TEXT NOT NULL,
    duration    REAL NOT NULL,
    cost_usd    REAL NOT NULL DEFAULT 0,
    environment TEXT NOT NULL,
    params      TEXT NOT NULL,
    metrics     TEXT NOT NULL,
    label       TEXT,
    group_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(dataset, task_id);
CREATE INDEX IF NOT EXISTS idx_runs_group ON runs(group_id);

CREATE TABLE IF NOT EXISTS steps (
    run_id   TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name     TEXT NOT NULL,
    duration REAL NOT NULL,
    PRIMARY KEY (run_id, position)
);
"""


@dataclass
class Run:
    kind: str
    source: str
    status: str
    duration: float
    commit_sha: str = ""
    dataset: str | None = None
    task_id: str | None = None
    cost_usd: float = 0.0
    label: str = ""
    group_id: str = ""
    environment: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    steps: list[tuple[str, float]] = field(default_factory=list)
    id: str = ""
    created_at: str = ""


class Store:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Base en version {version}, Robot ne connaît que {SCHEMA_VERSION}")
        if version >= 1:
            self._add_missing_columns("runs", {"label": "TEXT", "group_id": "TEXT"})
        self._db.executescript(SCHEMA)
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._db.commit()

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in existing:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    def save(self, run: Run) -> str:
        run.id = run.id or uuid.uuid4().hex
        run.created_at = run.created_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

        with self._db:
            self._db.execute(
                "INSERT INTO runs VALUES (:id, :created_at, :kind, :dataset, :task_id, :source,"
                " :commit_sha, :status, :duration, :cost_usd, :environment, :params, :metrics,"
                " :label, :group_id)",
                {
                    "id": run.id, "created_at": run.created_at, "kind": run.kind,
                    "dataset": run.dataset, "task_id": run.task_id, "source": run.source,
                    "commit_sha": run.commit_sha, "status": run.status,
                    "duration": round(run.duration, 2), "cost_usd": run.cost_usd,
                    "environment": _dumps(run.environment), "params": _dumps(run.params),
                    "metrics": _dumps(run.metrics), "label": run.label, "group_id": run.group_id,
                },
            )
            self._db.executemany(
                "INSERT INTO steps VALUES (?, ?, ?, ?)",
                [(run.id, i, name, round(duration, 2)) for i, (name, duration) in enumerate(run.steps)],
            )
        return run.id

    def get(self, run_id: str) -> Run | None:
        row = self._db.execute(
            "SELECT * FROM runs WHERE id = ? OR id LIKE ? || '%'", (run_id, run_id)
        ).fetchone()
        if row is None:
            return None
        steps = self._db.execute(
            "SELECT name, duration FROM steps WHERE run_id = ? ORDER BY position", (row["id"],)
        ).fetchall()
        return _to_run(row, [(s["name"], s["duration"]) for s in steps])

    def recent(self, limit: int = 20, kind: str | None = None, dataset: str | None = None) -> list[Run]:
        query = "SELECT * FROM runs WHERE (:kind IS NULL OR kind = :kind)" \
                " AND (:dataset IS NULL OR dataset = :dataset) ORDER BY created_at DESC LIMIT :limit"
        rows = self._db.execute(query, {"kind": kind, "dataset": dataset, "limit": limit}).fetchall()
        return [_to_run(row, []) for row in rows]

    def groups(self, kind: str = "eval", dataset: str | None = None) -> list[dict]:
        rows = self._db.execute(
            "SELECT group_id, label, MIN(created_at) AS created_at, COUNT(*) AS total,"
            " SUM(status = 'ok') AS ok, SUM(duration) AS duration,"
            " json_extract(params, '$.seed') AS seed"
            " FROM runs WHERE kind = :kind AND (:dataset IS NULL OR dataset = :dataset)"
            " AND group_id IS NOT NULL AND group_id != ''"
            " GROUP BY group_id ORDER BY created_at",
            {"kind": kind, "dataset": dataset},
        ).fetchall()
        return [dict(row) for row in rows]

    def find_group(self, name: str) -> str | None:
        row = self._db.execute(
            "SELECT group_id FROM runs WHERE group_id = ? OR group_id LIKE ? || '%' OR label = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (name, name, name),
        ).fetchone()
        return row["group_id"] if row else None

    def runs_in_group(self, group_id: str) -> list[Run]:
        rows = self._db.execute(
            "SELECT * FROM runs WHERE group_id = ? ORDER BY task_id", (group_id,)
        ).fetchall()
        return [_to_run(row, []) for row in rows]

    def results_for(self, dataset: str, seed: int, kind: str = "eval") -> list[Run]:
        rows = self._db.execute(
            "SELECT * FROM runs WHERE kind = ? AND dataset = ? AND status = 'ok'"
            " AND json_extract(params, '$.seed') = ? ORDER BY created_at",
            (kind, dataset, seed),
        ).fetchall()
        latest = {row["task_id"]: row for row in rows}
        return [_to_run(row, []) for row in latest.values()]

    def completed_tasks(self, dataset: str, seed: int, kind: str = "eval") -> set[str]:
        rows = self._db.execute(
            "SELECT DISTINCT task_id FROM runs WHERE kind = ? AND dataset = ? AND status = 'ok'"
            " AND json_extract(params, '$.seed') = ?",
            (kind, dataset, seed),
        ).fetchall()
        return {row["task_id"] for row in rows if row["task_id"]}

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _dumps(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _to_run(row: sqlite3.Row, steps: list[tuple[str, float]]) -> Run:
    return Run(
        id=row["id"], created_at=row["created_at"], kind=row["kind"], dataset=row["dataset"],
        task_id=row["task_id"], source=row["source"], commit_sha=row["commit_sha"],
        status=row["status"], duration=row["duration"], cost_usd=row["cost_usd"],
        environment=json.loads(row["environment"]), params=json.loads(row["params"]),
        metrics=json.loads(row["metrics"]), steps=steps,
        label=row["label"] or "", group_id=row["group_id"] or "",
    )
