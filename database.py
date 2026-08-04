"""
database.py — SQLite persistence layer for the agent system.
"""
import sqlite3
import json
import time
import os
import threading
from pathlib import Path
from datetime import datetime

ROOT_FOLDER = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_FOLDER, "agent.db")


class AgentDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _execute(self, sql, params=(), commit=False):
        with self._lock:
            cursor = self._conn.execute(sql, params)
            if commit:
                self._conn.commit()
            return cursor

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS thoughts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL,
                source      TEXT    NOT NULL,
                text        TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS llm_calls (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                model        TEXT    NOT NULL,
                provider     TEXT    NOT NULL,
                trigger      TEXT,
                prompt_chars INTEGER,
                response_chars INTEGER,
                latency_ms   INTEGER,
                error        TEXT
            );

            CREATE TABLE IF NOT EXISTS research_cache (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                folder_path  TEXT    NOT NULL,
                file_count   INTEGER,
                root_chars   INTEGER,
                research_chars INTEGER,
                payload      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS actions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL,
                trigger     TEXT    NOT NULL,
                action_text TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                trigger      TEXT    NOT NULL,
                full_text    TEXT    NOT NULL,
                action_part  TEXT
            );

            CREATE TABLE IF NOT EXISTS file_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content TEXT NOT NULL,
                size INTEGER,
                mtime REAL,
                last_accessed REAL,
                UNIQUE(folder_path, relative_path)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_thoughts_ts   ON thoughts(ts);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_ts  ON llm_calls(ts);
            CREATE INDEX IF NOT EXISTS idx_actions_ts    ON actions(ts);
            CREATE INDEX IF NOT EXISTS idx_thoughts_source ON thoughts(source);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_trigger ON llm_calls(trigger);
            CREATE INDEX IF NOT EXISTS idx_actions_trigger ON actions(trigger);
            CREATE INDEX IF NOT EXISTS idx_file_cache_path ON file_cache(folder_path, relative_path);
        """)
        self._conn.commit()

    def get_cached_file(self, folder_path: str, relative_path: str,
                        max_chars: int = None, current_mtime: float = None) -> str | None:
        row = self._execute(
            "SELECT content, mtime FROM file_cache WHERE folder_path=? AND relative_path=?",
            (folder_path, relative_path)
        ).fetchone()
        if row and (current_mtime is None or row["mtime"] == current_mtime):
            content = row["content"]
            self._execute(
                "UPDATE file_cache SET last_accessed=? WHERE folder_path=? AND relative_path=?",
                (time.time(), folder_path, relative_path),
                commit=True
            )
            if max_chars and len(content) > max_chars:
                return content[:max_chars]
            return content
        return None

    def save_file_to_cache(self, folder_path: str, relative_path: str,
                           content: str, size: int, mtime: float):
        self._execute(
            """INSERT OR REPLACE INTO file_cache
            (folder_path, relative_path, content, size, mtime, last_accessed)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (folder_path, relative_path, content, size, mtime, time.time()),
            commit=True
        )

    def clear_file_cache(self, folder_path: str = None):
        if folder_path:
            self._execute("DELETE FROM file_cache WHERE folder_path=?", (folder_path,))
        else:
            self._execute("DELETE FROM file_cache")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Thoughts
    # ------------------------------------------------------------------
    def log_thought(self, source: str, text: str):
        self._execute(
            "INSERT INTO thoughts (ts, source, text) VALUES (?, ?, ?)",
            (time.time(), source, text),
            commit=True
        )

    def get_thoughts(self, limit: int = 500, source: str = None) -> list[dict]:
        if source:
            rows = self._execute(
                "SELECT ts, source, text FROM thoughts WHERE source=? ORDER BY ts DESC LIMIT ?",
                (source, limit)
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT ts, source, text FROM thoughts ORDER BY ts DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------
    def log_llm_call(self, model: str, provider: str, trigger: str,
                     prompt_chars: int, response_chars: int,
                     latency_ms: int, error: str = None):
        self._execute(
            """INSERT INTO llm_calls
               (ts, model, provider, trigger, prompt_chars, response_chars, latency_ms, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), model, provider, trigger,
             prompt_chars, response_chars, latency_ms, error),
            commit=True
        )

    def get_llm_stats(self) -> dict:
        row = self._execute("""
            SELECT
                COUNT(*)                            AS total_calls,
                SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) AS successes,
                SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
                AVG(latency_ms)                     AS avg_latency_ms,
                SUM(prompt_chars + response_chars)  AS total_chars
            FROM llm_calls
        """).fetchone()
        return dict(row) if row else {}

    def get_recent_llm_calls(self, limit: int = 50) -> list[dict]:
        rows = self._execute(
            """SELECT ts, model, provider, trigger, prompt_chars,
                      response_chars, latency_ms, error
               FROM llm_calls ORDER BY ts DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Research cache
    # ------------------------------------------------------------------
    def save_research_cache(self, research: dict):
        folder = research.get("research_path") or "root_only"
        self._execute(
            """INSERT INTO research_cache
               (ts, folder_path, file_count, root_chars, research_chars, payload)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                time.time(),
                folder,
                research.get("research_file_count", 0),
                len(research.get("root", "")),
                len(research.get("research", "")),
                json.dumps(research),
            ),
            commit=True
        )

    def load_latest_research_cache(self, folder_path: str) -> dict | None:
        row = self._execute(
            """SELECT payload FROM research_cache
               WHERE folder_path=? ORDER BY ts DESC LIMIT 1""",
            (folder_path,)
        ).fetchone()
        if row:
            try:
                return json.loads(row["payload"])
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # Actions & decisions
    # ------------------------------------------------------------------
    def log_action(self, trigger: str, action_text: str):
        self._execute(
            "INSERT INTO actions (ts, trigger, action_text) VALUES (?, ?, ?)",
            (time.time(), trigger, action_text),
            commit=True
        )

    def log_decision(self, trigger: str, full_text: str, action_part: str = None):
        self._execute(
            """INSERT INTO decisions (ts, trigger, full_text, action_part)
               VALUES (?, ?, ?, ?)""",
            (time.time(), trigger, full_text, action_part),
            commit=True
        )

    def get_recent_actions(self, limit: int = 20) -> list[dict]:
        rows = self._execute(
            "SELECT ts, trigger, action_text FROM actions ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------
    def ts_to_str(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    def close(self):
        self._conn.close()