"""SQLite persistence: conversation history, paused runs, reminders, escalations, audit log."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_thread ON messages(thread_id, id);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, user_id TEXT NOT NULL, channel_id TEXT NOT NULL,
  status TEXT NOT NULL, state TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, channel_id TEXT NOT NULL,
  due_at TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  idem_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS escalations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, channel_id TEXT NOT NULL,
  reason TEXT NOT NULL, summary TEXT NOT NULL, message_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, run_id TEXT NOT NULL, user_id TEXT NOT NULL,
  tool TEXT NOT NULL, tier TEXT NOT NULL, args TEXT NOT NULL, decision TEXT NOT NULL,
  approver_id TEXT, outcome TEXT NOT NULL, detail TEXT
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


class Store:
    def __init__(self, path: str = ":memory:", clock=lambda: datetime.now(UTC)):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.clock = clock
        self._lock = threading.Lock()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    # ---- conversation history (user / assistant text only; tool traffic lives in traces)
    def append_message(self, thread_id: str, role: str, content: str) -> None:
        self._exec(
            "INSERT INTO messages(thread_id, role, content, created_at) VALUES (?,?,?,?)",
            (thread_id, role, content, _iso(self.clock())),
        )

    def history(self, thread_id: str, max_turns: int = 8) -> list[dict]:
        rows = self._all(
            "SELECT role, content FROM messages WHERE thread_id=? ORDER BY id DESC LIMIT ?", (thread_id, 2 * max_turns)
        )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # ---- runs (a paused run's full state is stored so it can resume after approval)
    def save_run(self, run_id: str, thread_id: str, user_id: str, channel_id: str, status: str, state: Any) -> None:
        now = _iso(self.clock())
        self._exec(
            """INSERT INTO runs(run_id, thread_id, user_id, channel_id, status, state, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, state=excluded.state, updated_at=excluded.updated_at""",
            (
                run_id,
                thread_id,
                user_id,
                channel_id,
                status,
                json.dumps(state) if state is not None else None,
                now,
                now,
            ),
        )

    def get_run(self, run_id: str) -> dict | None:
        rows = self._all("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if not rows:
            return None
        r = rows[0]
        r["state"] = json.loads(r["state"]) if r["state"] else None
        return r

    # ---- reminders (idempotent: the same user, time and text never creates two rows)
    def create_reminder(self, user_id: str, channel_id: str, due_at: datetime, text: str) -> tuple[int, bool]:
        key = hashlib.sha256(f"{user_id}|{_iso(due_at)}|{text.strip().lower()}".encode()).hexdigest()
        existing = self._all("SELECT id FROM reminders WHERE idem_key=?", (key,))
        if existing:
            return existing[0]["id"], False
        cur = self._exec(
            "INSERT INTO reminders(user_id, channel_id, due_at, text, idem_key, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, channel_id, _iso(due_at), text, key, _iso(self.clock())),
        )
        return cur.lastrowid, True

    def reminders(self, user_id: str | None = None, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM reminders WHERE 1=1", []
        if user_id:
            sql += " AND user_id=?"
            params.append(user_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        return self._all(sql + " ORDER BY due_at", tuple(params))

    def due_reminders(self, now: datetime) -> list[dict]:
        return self._all("SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at", (_iso(now),))

    def mark_reminder_sent(self, rid: int) -> None:
        self._exec("UPDATE reminders SET status='sent' WHERE id=?", (rid,))

    # ---- escalations
    def create_escalation(
        self, user_id: str, channel_id: str, reason: str, summary: str, message_id: str | None
    ) -> int:
        cur = self._exec(
            "INSERT INTO escalations(user_id, channel_id, reason, summary, message_id, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, channel_id, reason, summary, message_id, _iso(self.clock())),
        )
        return cur.lastrowid

    def count_escalations(self, user_id: str, since: datetime) -> int:
        return self._all(
            "SELECT COUNT(*) AS n FROM escalations WHERE user_id=? AND created_at>=?", (user_id, _iso(since))
        )[0]["n"]

    def escalations(self) -> list[dict]:
        return self._all("SELECT * FROM escalations ORDER BY id")

    # ---- audit log: one row per tool call that was proposed, whatever happened to it
    def audit(
        self,
        run_id: str,
        user_id: str,
        tool: str,
        tier: str,
        args: dict,
        decision: str,
        outcome: str,
        approver_id: str | None = None,
        detail: str = "",
    ) -> None:
        self._exec(
            """INSERT INTO audit(ts, run_id, user_id, tool, tier, args, decision, approver_id, outcome, detail)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                _iso(self.clock()),
                run_id,
                user_id,
                tool,
                tier,
                json.dumps(args, default=str),
                decision,
                approver_id,
                outcome,
                detail,
            ),
        )

    def audit_log(self, run_id: str | None = None) -> list[dict]:
        if run_id:
            return self._all("SELECT * FROM audit WHERE run_id=? ORDER BY id", (run_id,))
        return self._all("SELECT * FROM audit ORDER BY id")
