"""
agents/summarizer.py — Enhanced SummarizerThread

Improvements over v1:
  ─────────────────────────────────────────────────────
  • Own SQLite database (summarizer.db) — separate from agent.db
  • Conversation history stored & retrieved across sessions
  • Speech pattern learning (SpeechPatternBank from library)
    – Observes every human message
    – Style description shown in UI
    – LLM prompted to match the user's communication style
  • Human ↔ Summarizer chat
    – send_human_message(text)  →  chat_reply Signal(str, str)
    – Full conversation memory with rolling window
    – Quick-action shortcuts
  • Configurable summarization strategy:
    – "brief"   : one-sentence
    – "standard": paragraph
    – "detailed": bullets + next-step
  • EmbeddingCache dedup — skip summarising near-identical thought batches
  • Graceful degradation when LLM unavailable
  • Topic tracking — logs what was discussed per session
  ─────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import json
import queue
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

from PySide6.QtCore import QThread, Signal

# ── library imports ────────────────────────────────────────────────────────────
from library import (
    ConversationMemory,
    SpeechPatternBank,
    EmbeddingCache,
    AgentLogger,
    fingerprint,
    ts_now,
)

ROOT_FOLDER  = os.path.dirname(os.path.abspath(__file__))
SUMM_DB_PATH = os.path.join(ROOT_FOLDER, "summarizer.db")


# ═══════════════════════════════════════════════════════════════════════════
#  SummarizerDB  — dedicated persistence layer
# ═══════════════════════════════════════════════════════════════════════════
class SummarizerDB:
    """
    SQLite database dedicated to the summarizer.
    Stores:
      - summaries          : timestamped summaries with topic tags
      - chat_history       : human ↔ summarizer conversation log
      - speech_patterns    : serialised SpeechPatternBank export per session
      - topic_index        : rolling topic frequency table
    """

    def __init__(self, db_path: str = SUMM_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _execute(self, sql, params=(), commit=False):
        with self._lock:
            cur = self._conn.execute(sql, params)
            if commit:
                self._conn.commit()
            return cur

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS summaries (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        REAL    NOT NULL,
                text      TEXT    NOT NULL,
                strategy  TEXT    DEFAULT 'standard',
                topics    TEXT    DEFAULT '',
                fp        TEXT    DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS chat_history (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        REAL    NOT NULL,
                role      TEXT    NOT NULL,
                text      TEXT    NOT NULL,
                session   TEXT    DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS speech_patterns (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        REAL    NOT NULL,
                session   TEXT    NOT NULL,
                payload   TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS topic_index (
                topic     TEXT    PRIMARY KEY,
                count     INTEGER DEFAULT 1,
                last_seen REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_summaries_ts      ON summaries(ts);
            CREATE INDEX IF NOT EXISTS idx_chat_history_ts   ON chat_history(ts);
            CREATE INDEX IF NOT EXISTS idx_topic_count       ON topic_index(count DESC);
        """)
        self._conn.commit()

    # ── Summaries ─────────────────────────────────────────────────────────────
    def save_summary(self, text: str, strategy: str = "standard",
                     topics: List[str] = None, fp: str = ""):
        self._execute(
            "INSERT INTO summaries (ts, text, strategy, topics, fp) VALUES (?,?,?,?,?)",
            (time.time(), text, strategy, json.dumps(topics or []), fp),
            commit=True
        )

    def get_recent_summaries(self, limit: int = 20) -> List[Dict]:
        rows = self._execute(
            "SELECT ts, text, strategy, topics FROM summaries ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_summary_count(self) -> int:
        return self._execute("SELECT COUNT(*) FROM summaries").fetchone()[0]

    # ── Chat history ──────────────────────────────────────────────────────────
    def save_chat_turn(self, role: str, text: str, session: str = ""):
        self._execute(
            "INSERT INTO chat_history (ts, role, text, session) VALUES (?,?,?,?)",
            (time.time(), role, text, session),
            commit=True
        )

    def get_recent_chat(self, limit: int = 40, session: str = None) -> List[Dict]:
        if session:
            rows = self._execute(
                "SELECT ts, role, text FROM chat_history "
                "WHERE session=? ORDER BY ts DESC LIMIT ?",
                (session, limit)
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT ts, role, text FROM chat_history ORDER BY ts DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── Speech patterns ───────────────────────────────────────────────────────
    def save_speech_patterns(self, session: str, bank: SpeechPatternBank):
        payload = json.dumps(bank.export())
        self._execute(
            "INSERT INTO speech_patterns (ts, session, payload) VALUES (?,?,?)",
            (time.time(), session, payload),
            commit=True
        )

    def load_latest_speech_patterns(self, session: str = "") -> Optional[Dict]:
        row = self._execute(
            "SELECT payload FROM speech_patterns ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row:
            try:
                return json.loads(row["payload"])
            except Exception:
                pass
        return None

    # ── Topics ────────────────────────────────────────────────────────────────
    def update_topics(self, topics: List[str]):
        for topic in topics:
            t = topic.lower().strip()
            if not t:
                continue
            self._execute(
                """INSERT INTO topic_index (topic, count, last_seen) VALUES (?,1,?)
                   ON CONFLICT(topic) DO UPDATE SET
                     count = count + 1, last_seen = excluded.last_seen""",
                (t, time.time()),
                commit=False
            )
        self._conn.commit()

    def get_top_topics(self, limit: int = 10) -> List[Dict]:
        rows = self._execute(
            "SELECT topic, count FROM topic_index ORDER BY count DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self._conn.close()


# ═══════════════════════════════════════════════════════════════════════════
#  SummarizerThread
# ═══════════════════════════════════════════════════════════════════════════
class SummarizerThread(QThread):
    """
    Background thread that:
      1. Watches manager/agent/comms thought streams
      2. Periodically distils them into plain-English summaries
      3. Answers direct human chat messages
      4. Learns the human's speech patterns and mirrors them
    """

    summary_ready   = Signal(str)          # new auto-summary text
    chat_reply      = Signal(str, str)     # (label, text) — label = "Answer"|"System"
    status_changed  = Signal(str, str)     # (status, task)
    paused_changed  = Signal(bool)
    style_updated   = Signal(str)          # human-readable style description

    # ── Summarization strategy prompts ──────────────────────────────────────
    _STRATEGY_PROMPTS = {
        "brief": (
            "Summarise what the agents are doing in ONE plain sentence. "
            "No jargon.  Start with a verb."
        ),
        "standard": (
            "Write a short paragraph (3-5 sentences) explaining what is "
            "happening, what was decided, and what the next step is. "
            "Use plain language — no technical jargon."
        ),
        "detailed": (
            "Write a structured summary with three bullet points:\n"
            "• What happened\n"
            "• What was decided\n"
            "• Next steps\n"
            "Keep each bullet to one sentence.  Plain language."
        ),
    }

    _CHAT_SYSTEM = (
            "You are an assistant embedded in MrBot1000, an AI program for real-time earning opportunity discovery. "
            "You watch the agent's work and can explain the system architecture, models, and tasks. "
            "You have access to recent summaries and conversation history. "
            "Be helpful, accurate, and conversational. Max 250 words unless asked for more. "
            "Always explain technical details clearly. Do not invent capabilities or fake earnings. "
            "If asked about technical details, answer accurately based on your knowledge."
        )

    def __init__(self, worker, db=None):
        super().__init__()
        self.worker   = worker
        self.main_db  = db                    # shared agent.db (optional)
        self.summ_db  = SummarizerDB()        # own database

        # Session ID for this run
        self._session = datetime.now().strftime("%Y%m%d_%H%M%S")

        # ── Thought queues ─────────────────────────────────────────────────
        self.manager_queue = queue.Queue()
        self.agent_queue   = queue.Queue()
        self.comms_queue   = queue.Queue()
        self._chat_queue   = queue.Queue()    # human → summarizer chat

        # ── State ─────────────────────────────────────────────────────────
        self.running = True
        self.paused  = False
        self.pending_thoughts: List[str] = []
        self.last_thought_time = time.time()

        # ── NLP helpers ───────────────────────────────────────────────────
        self._speech_bank    = SpeechPatternBank(max_samples=300)
        self._embedding_cache = EmbeddingCache(similarity_threshold=0.85,
                                               max_size=150)
        self._conversation   = ConversationMemory(max_chars=8000, max_turns=30)
        self._logger         = AgentLogger(db=db, source="Summarizer",
                                           signal=None)

        # ── Configuration ─────────────────────────────────────────────────
        self.interval      = 2.0      # poll interval (seconds)
        self.cooldown      = 5.0      # quiet time before summarising
        self.min_thoughts  = 5        # minimum new thoughts to trigger summary
        self.max_tokens    = 250
        self.max_thoughts  = 20
        self.strategy      = "standard"   # "brief" | "standard" | "detailed"

        self.last_summary  = ""
        self._last_summary_fp = ""

        # ── Restore speech patterns from previous session ─────────────────
        saved_patterns = self.summ_db.load_latest_speech_patterns()
        if saved_patterns:
            try:
                self._speech_bank.import_stats(saved_patterns)
            except Exception:
                pass

        # ── Load chat history into conversation memory ─────────────────────
        recent_chat = self.summ_db.get_recent_chat(limit=20)
        for turn in recent_chat:
            self._conversation.add(turn["role"], turn["text"])

    # ─────────────────────────────────────────────────────────────────────────
    #  Public input methods (called from main thread)
    # ─────────────────────────────────────────────────────────────────────────

    def add_manager_thought(self, text: str):
        self.manager_queue.put(("manager", text))
        self._thought_arrived()

    def add_agent_thought(self, text: str):
        self.agent_queue.put(("agent", text))
        self._thought_arrived()

    def add_comms_thought(self, direction: str, text: str):
        self.comms_queue.put(("comms", f"[{direction}] {text}"))
        self._thought_arrived()

    def send_human_message(self, text: str):
        """Queue a human chat message for the summarizer to answer."""
        self._chat_queue.put(text)

    def _thought_arrived(self):
        self.last_thought_time = time.time()

    # ─────────────────────────────────────────────────────────────────────────
    #  Configuration
    # ─────────────────────────────────────────────────────────────────────────

    def set_paused(self, paused: bool):
        if self.paused != paused:
            self.paused = paused
            self.paused_changed.emit(paused)
            self.status_changed.emit("Paused" if paused else "Idle", "")

    def set_strategy(self, strategy: str):
        """Change summarization depth: 'brief' | 'standard' | 'detailed'"""
        if strategy in self._STRATEGY_PROMPTS:
            self.strategy = strategy
            self.status_changed.emit("Idle", f"Strategy: {strategy}")

    def update_config(self, interval=None, cooldown=None, min_thoughts=None,
                      max_tokens=None, max_thoughts=None, strategy=None):
        if interval     is not None: self.interval     = interval
        if cooldown     is not None: self.cooldown     = cooldown
        if min_thoughts is not None: self.min_thoughts = min_thoughts
        if max_tokens   is not None: self.max_tokens   = max_tokens
        if max_thoughts is not None: self.max_thoughts = max_thoughts
        if strategy     is not None: self.set_strategy(strategy)

    # ─────────────────────────────────────────────────────────────────────────
    #  Main loop
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        self.status_changed.emit("Idle", "Watching thoughts…")

        while self.running:
            # 1. Handle direct human chat (always, even when paused)
            try:
                human_text = self._chat_queue.get_nowait()
                self._handle_chat(human_text)
            except queue.Empty:
                pass

            if not self.paused:
                # 2. Drain thought queues
                for q in (self.manager_queue, self.agent_queue,
                          self.comms_queue):
                    while True:
                        try:
                            _source, text = q.get_nowait()
                            self.pending_thoughts.append(text)
                        except queue.Empty:
                            break

                # 3. Check summarization trigger
                now = time.time()
                if (len(self.pending_thoughts) >= self.min_thoughts
                        and (now - self.last_thought_time) >= self.cooldown):
                    self._summarize()

            time.sleep(self.interval)

    # ─────────────────────────────────────────────────────────────────────────
    #  Summarization
    # ─────────────────────────────────────────────────────────────────────────

    def _summarize(self):
        if not self.pending_thoughts:
            return

        thoughts_to_use = self.pending_thoughts[-self.max_thoughts:]
        combined = "\n".join(thoughts_to_use)

        # Dedup: skip if very similar to previous batch
        fp = fingerprint(combined)
        if fp == self._last_summary_fp:
            self.pending_thoughts.clear()
            return
        cached = self._embedding_cache.get(combined)
        if cached and cached == self.last_summary:
            self.pending_thoughts.clear()
            return

        count = len(self.pending_thoughts)
        self.status_changed.emit("Summarizing",
                                 f"Processing {count} thoughts [{self.strategy}]")

        strategy_instruction = self._STRATEGY_PROMPTS[self.strategy]
        style_instruction    = self._speech_bank.as_prompt_instruction()

        system = (
            "You are a helpful assistant that simplifies technical AI agent activity.\n"
            f"{strategy_instruction}\n"
            + (f"\nStyle guidance: {style_instruction}" if style_instruction else "")
        )

        user_prompt = (
            f"Thoughts from agents:\n{combined}\n\n"
            f"Simple explanation:"
        )

        try:
            # For chat mode, use the chat model (faster, smaller)
            summary = self.worker.llm(system=system, user=user_prompt,
                                      max_tokens=self.max_tokens, chat=True)
            if summary and not summary.startswith("ERROR:"):
                # Extract topics (simple keyword extraction)
                topics = self._extract_topics(combined)

                # Persist
                self.summ_db.save_summary(
                    summary, strategy=self.strategy, topics=topics, fp=fp)
                self.summ_db.update_topics(topics)

                # Emit
                if summary != self.last_summary:
                    self.summary_ready.emit(summary)
                    self.last_summary = summary
                    self._last_summary_fp = fp
                    self._embedding_cache.put(combined, summary)

                    # Add to conversation memory so chat can reference it
                    self._conversation.add("system",
                                           f"[Latest summary] {summary}")
        except Exception as e:
            self._logger.error(f"Summarization error: {e}")
        finally:
            self.pending_thoughts.clear()
            self.status_changed.emit("Idle", "Watching thoughts…")

    def _extract_topics(self, text: str) -> List[str]:
        """
        Simple keyword-based topic extraction (no external ML needed).
        Looks for capitalised words and domain keywords.
        """
        DOMAIN_KEYWORDS = {
            "proposer", "evaluator", "manager", "agent", "research",
            "file", "bug", "fix", "refactor", "error", "action",
            "heartbeat", "scan", "improve", "write", "read", "cache",
            "database", "security", "task", "decision", "response"
        }
        words = set(text.lower().split())
        return [kw for kw in DOMAIN_KEYWORDS if kw in words][:8]

    # ─────────────────────────────────────────────────────────────────────────
    #  Human ↔ Summarizer Chat
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_chat(self, human_text: str):
        """Process a human message and emit a chat reply."""
        self.status_changed.emit("Chatting", "Answering…")

        # Observe speech patterns
        self._speech_bank.observe(human_text)
        style_desc = self._speech_bank.describe()
        self.style_updated.emit(style_desc)

        # Persist speech pattern periodically (every 10 messages)
        if self._speech_bank.sample_count % 10 == 0:
            try:
                self.summ_db.save_speech_patterns(self._session,
                                                  self._speech_bank)
            except Exception:
                pass

        # Save to DB
        self.summ_db.save_chat_turn("user", human_text, self._session)

        # Add to in-memory conversation
        self._conversation.add("user", human_text)

        # Build context: recent summaries + conversation history
        recent_summaries = self.summ_db.get_recent_summaries(limit=5)
        summ_context = "\n".join(
            f"[{self._fmt_ts(s['ts'])}] {s['text']}"
            for s in recent_summaries
        )
        top_topics = self.summ_db.get_top_topics(limit=8)
        topics_str = ", ".join(t["topic"] for t in top_topics) or "none yet"

        # Style instruction
        style_instruction = self._speech_bank.as_prompt_instruction()

        system_prompt = (
            f"{self._CHAT_SYSTEM}\n\n"
            f"Frequent topics seen: {topics_str}.\n"
            + (f"Adapt your reply style: {style_instruction}" if style_instruction else "")
        )

        conversation_str = self._conversation.render(include_timestamps=False)

        user_prompt = (
            f"RECENT AGENT SUMMARIES:\n{summ_context}\n\n"
            f"CONVERSATION SO FAR:\n{conversation_str}\n\n"
            f"Human: {human_text}\nSummarizer:"
        )

        try:
            reply = self.worker.llm(system=system_prompt,
                                    user=user_prompt,
                                    max_tokens=350,
                                    chat=True)
            if not reply or reply.startswith("ERROR:"):
                self.worker.log_signal.emit(
                    f"[Summarizer] LLM chat call failed or empty: {reply}"
                )
                reply = ("I'm having trouble reaching the LLM right now. "
                         "Please check your API connection.")
        except Exception as e:
            self.worker.log_signal.emit(f"[Summarizer] LLM exception: {e}")
            reply = f"Error generating reply: {e}"

        # Persist reply
        self.summ_db.save_chat_turn("assistant", reply, self._session)
        self._conversation.add("assistant", reply)

        self.chat_reply.emit("Summarizer", reply)
        self.status_changed.emit("Idle", "Watching thoughts…")

    @staticmethod
    def _fmt_ts(ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")

    # ─────────────────────────────────────────────────────────────────────────
    #  Public helpers
    # ─────────────────────────────────────────────────────────────────────────

    def get_chat_history(self, limit: int = 40) -> List[Dict]:
        """Return recent chat history from the summarizer DB."""
        return self.summ_db.get_recent_chat(limit=limit)

    def get_top_topics(self, limit: int = 10) -> List[Dict]:
        """Return the most frequently discussed topics."""
        return self.summ_db.get_top_topics(limit=limit)

    def get_style_description(self) -> str:
        """Return a description of the detected speech style."""
        return self._speech_bank.describe()

    def get_summary_stats(self) -> Dict:
        return {
            "total_summaries": self.summ_db.get_summary_count(),
            "speech_samples":  self._speech_bank.sample_count,
            "formality":       self._speech_bank.formality_label(),
            "avg_sentence_len": round(self._speech_bank.avg_sentence_length(), 1),
            "strategy":        self.strategy,
            "top_topics":      self.get_top_topics(5),
        }

    def clear_chat_history(self):
        """Clear in-memory conversation (DB history is kept)."""
        self._conversation.clear()

    def stop(self):
        self.running = False
        try:
            self.summ_db.close()
        except Exception:
            pass