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

            CREATE TABLE IF NOT EXISTS proposals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                gig_title    TEXT,
                platform     TEXT,
                budget_usd   REAL,
                draft        TEXT    NOT NULL,
                status       TEXT    DEFAULT 'drafted'
            );

            CREATE INDEX IF NOT EXISTS idx_proposals_ts ON proposals(ts);

            -- v2.0.22 S1: instruction provenance gate (untrusted external playbooks)
            CREATE TABLE IF NOT EXISTS instruction_quarantine (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                url          TEXT    NOT NULL,
                kind         TEXT    DEFAULT 'skill.md',
                title        TEXT,
                content_hash TEXT    NOT NULL,
                content      TEXT    NOT NULL,
                status       TEXT    DEFAULT 'pending'  -- pending|allowed|blocked
            );

            CREATE TABLE IF NOT EXISTS instruction_allowlist (
                url          TEXT PRIMARY KEY,
                content_hash TEXT,
                title        TEXT,
                approved_at  REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS instruction_blacklist (
                url          TEXT PRIMARY KEY,
                content_hash TEXT,
                related      TEXT,    -- related identifiers (url + skill.md + related)
                rejected_at  REAL    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_inst_q_status ON instruction_quarantine(status);
            CREATE INDEX IF NOT EXISTS idx_inst_q_hash   ON instruction_quarantine(content_hash);

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
                COALESCE(COUNT(*), 0) AS total_calls,
                COALESCE(SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END), 0) AS successes,
                COALESCE(SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END), 0) AS errors,
                COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
                COALESCE(SUM(prompt_chars + response_chars), 0) AS total_chars
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
    # Drafted proposals (v2.0.21 P2#4)
    # ------------------------------------------------------------------
    def add_proposal(self, gig_title: str = None, platform: str = None,
                     budget_usd: float = 0.0, draft: str = "",
                     status: str = "drafted") -> int:
        """Persist a drafted gig proposal so work survives restart and shows in
        DB Stats. Returns the new row id."""
        cur = self._execute(
            """INSERT INTO proposals
               (ts, gig_title, platform, budget_usd, draft, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (time.time(), gig_title, platform, float(budget_usd or 0), draft, status),
            commit=True
        )
        return cur.lastrowid

    def get_proposals(self, limit: int = 100) -> list[dict]:
        rows = self._execute(
            """SELECT id, ts, gig_title, platform, budget_usd, status
               FROM proposals ORDER BY ts DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_proposals(self) -> int:
        row = self._execute("SELECT COUNT(*) AS n FROM proposals").fetchone()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Instruction provenance gate (v2.0.22 S1): untrusted external playbooks
    # discovered on platforms (e.g. remote SKILL.md). Never executed until a
    # human approves; rejected -> blacklisted (always ignored).
    # ------------------------------------------------------------------
    def in_instruction_allowlist(self, url: str) -> bool:
        row = self._execute(
            "SELECT 1 FROM instruction_allowlist WHERE url=?", (url,)
        ).fetchone()
        return row is not None

    def in_instruction_blacklist(self, url: str) -> bool:
        row = self._execute(
            "SELECT 1 FROM instruction_blacklist WHERE url=?", (url,)
        ).fetchone()
        return row is not None

    def add_quarantined_instruction(self, url: str, kind: str, title: str,
                                    content_hash: str, content: str) -> int:
        cur = self._execute(
            """INSERT INTO instruction_quarantine
               (ts, url, kind, title, content_hash, content, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (time.time(), url, kind, title, content_hash, content),
            commit=True
        )
        return cur.lastrowid

    def find_quarantined_by_hash(self, content_hash: str) -> dict | None:
        row = self._execute(
            "SELECT * FROM instruction_quarantine WHERE content_hash=? "
            "ORDER BY id DESC LIMIT 1",
            (content_hash,)
        ).fetchone()
        return dict(row) if row else None

    def review_instruction(self, quarantine_id: int, approve: bool,
                           url: str = "", content_hash: str = "",
                           related: str = "") -> str:
        """Move a pending instruction to allowed (allowlist) or blocked
        (blacklist). Returns the resulting status string."""
        now = time.time()
        if approve:
            self._execute(
                "UPDATE instruction_quarantine SET status='allowed' WHERE id=?",
                (quarantine_id,), commit=True)
            self._execute(
                """INSERT OR REPLACE INTO instruction_allowlist
                   (url, content_hash, title, approved_at)
                   VALUES (?, ?, '', ?)""",
                (url, content_hash, now), commit=True)
            return "allowed"
        else:
            self._execute(
                "UPDATE instruction_quarantine SET status='blocked' WHERE id=?",
                (quarantine_id,), commit=True)
            self._execute(
                """INSERT OR REPLACE INTO instruction_blacklist
                   (url, content_hash, related, rejected_at)
                   VALUES (?, ?, ?, ?)""",
                (url, content_hash, related, now), commit=True)
            return "blocked"

    def list_pending_instructions(self, limit: int = 100) -> list[dict]:
        rows = self._execute(
            "SELECT id, ts, url, kind, title, content_hash, status "
            "FROM instruction_quarantine WHERE status='pending' "
            "ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def count_instruction_lists(self) -> dict:
        q = self._execute("SELECT COUNT(*) n FROM instruction_quarantine").fetchone()
        a = self._execute("SELECT COUNT(*) n FROM instruction_allowlist").fetchone()
        b = self._execute("SELECT COUNT(*) n FROM instruction_blacklist").fetchone()
        return {"quarantined": int(q["n"]), "allowed": int(a["n"]),
                "blocked": int(b["n"]), "pending": len(self.list_pending_instructions())}

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