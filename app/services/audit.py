"""SQLite-backed audit log and research-run persistence."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from app.config import get_settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_settings().database_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._connect() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, question TEXT NOT NULL, status TEXT NOT NULL, report_markdown TEXT, started_at TEXT NOT NULL, finished_at TEXT);
            CREATE TABLE IF NOT EXISTS audit_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(run_id TEXT NOT NULL, source_id TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(run_id, source_id));
            CREATE TABLE IF NOT EXISTS claims(run_id TEXT NOT NULL, claim_id TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(run_id, claim_id));
            CREATE INDEX IF NOT EXISTS idx_audit_events_run_id_event_id ON audit_events(run_id, event_id);
            CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
            """)

    def start_run(self, run_id: str, question: str):
        with self._connect() as c:
            c.execute("INSERT INTO runs(run_id, question, status, started_at) VALUES(?,?,?,?)", (run_id, question, "running", utcnow()))
        self.log(run_id, "run_started", {"question": question})

    def log(self, run_id: str, event_type: str, payload: dict[str, Any]):
        with self._connect() as c:
            c.execute("INSERT INTO audit_events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)", (run_id, event_type, json.dumps(payload, ensure_ascii=False), utcnow()))

    def save_source(self, run_id: str, source: dict[str, Any]):
        with self._connect() as c:
            c.execute("INSERT OR REPLACE INTO sources(run_id,source_id,payload_json) VALUES(?,?,?)", (run_id, source["source_id"], json.dumps(source, ensure_ascii=False)))

    def save_claim(self, run_id: str, claim: dict[str, Any]):
        with self._connect() as c:
            c.execute("INSERT OR REPLACE INTO claims(run_id,claim_id,payload_json) VALUES(?,?,?)", (run_id, claim["claim_id"], json.dumps(claim, ensure_ascii=False)))

    def finish_run(self, run_id: str, status: str, report: str):
        with self._connect() as c:
            c.execute("UPDATE runs SET status=?, report_markdown=?, finished_at=? WHERE run_id=?", (status, report, utcnow(), run_id))
        self.log(run_id, "run_finished", {"status": status})

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute("SELECT event_type,payload_json,created_at FROM audit_events WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall()
        return [{"event_type": r["event_type"], "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]} for r in rows]

    def get_run_progress(self, run_id: str) -> dict[str, Any] | None:
        """Return a compact, content-free progress snapshot for polling clients."""
        tracked_events = (
            "agent_started",
            "agent_completed",
            "research_round_completed",
            "qdrant_retrieval",
            "claim_verified",
            "routing_decision",
            "run_failed",
            "run_finished",
        )
        placeholders = ",".join("?" for _ in tracked_events)
        with self._connect() as c:
            run = c.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run:
                return None
            rows = c.execute(
                f"SELECT event_type,payload_json,created_at FROM audit_events "
                f"WHERE run_id=? AND event_type IN ({placeholders}) ORDER BY event_id",
                (run_id, *tracked_events),
            ).fetchall()

        progress_events = []
        allowed_fields = {
            "agent_started": ("agent", "round"),
            "agent_completed": ("agent", "round", "output_count", "status_counts"),
            "qdrant_retrieval": (
                "enabled", "indexed_chunks", "retrieved_source_count", "error_type"
            ),
            "claim_verified": ("claim_id", "status"),
            "routing_decision": ("to", "gap_count"),
            "run_failed": ("error_type",),
            "run_finished": ("status",),
        }
        for row in rows:
            payload = json.loads(row["payload_json"])
            event_type = row["event_type"]
            if event_type == "research_round_completed":
                safe_payload = {
                    "round": payload.get("round"),
                    "total_sources": payload.get("total_sources"),
                    "query_count": len(payload.get("queries", [])),
                }
            else:
                safe_payload = {
                    key: payload[key]
                    for key in allowed_fields.get(event_type, ())
                    if key in payload
                }
            progress_events.append({
                "event_type": event_type,
                "payload": safe_payload,
                "created_at": row["created_at"],
            })
        return {
            "run_id": run_id,
            "status": run["status"],
            "audit_events": progress_events,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as c:
            run = c.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                return None
            sources = c.execute("SELECT payload_json FROM sources WHERE run_id=?", (run_id,)).fetchall()
            claims = c.execute("SELECT payload_json FROM claims WHERE run_id=?", (run_id,)).fetchall()
        return {"run_id": run["run_id"], "question": run["question"], "status": run["status"], "report_markdown": run["report_markdown"] or "", "sources": [json.loads(r[0]) for r in sources], "claims": [json.loads(r[0]) for r in claims], "audit_events": self.get_events(run_id)}
