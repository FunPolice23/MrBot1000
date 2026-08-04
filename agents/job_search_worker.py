"""
MrBot1000/agents/job_search_worker.py — JobSearchWorker

Specialization: finding, evaluating, and queuing freelance gigs.

Targets platforms like Reddit, Fiverr, Upwork, and social platforms.
The worker:
  1. Builds search queries from team skill profile
  2. Calls the LLM to generate simulated/reasoned job listings (when no live API)
  3. Evaluates each listing against team capabilities
  4. Scores and queues promising jobs for the Manager to assign
  5. Tracks applied/rejected/pending jobs in its own SQLite table

Plugs into WorkerRegistry automatically — drop this file in agents/ and it's found.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from library import AgentLogger, PromptBuilder, ResponseParser, ts_now, fingerprint
from agents.base_worker import WorkerAgent, ROOT_FOLDER

# ─────────────────────────────────────────────────────────────────────────────
#  Job record
# ─────────────────────────────────────────────────────────────────────────────
JOB_STATUSES = ("new", "evaluating", "queued", "assigned",
                "applied", "rejected", "won", "lost")


class JobRecord:
    def __init__(self, job_id: str, platform: str, title: str,
                 description: str, budget: float, skills: List[str],
                 url: str = ""):
        self.job_id     = job_id
        self.platform   = platform
        self.title      = title
        self.description = description
        self.budget     = budget          # USD estimate
        self.skills     = skills
        self.url        = url
        self.status     = "new"
        self.score      = 0.0             # fit score 0-1
        self.notes      = ""
        self.found_at   = time.time()
        self.assigned_to: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "job_id":      self.job_id,
            "platform":    self.platform,
            "title":       self.title,
            "description": self.description[:300],
            "budget":      self.budget,
            "skills":      self.skills,
            "url":         self.url,
            "status":      self.status,
            "score":       self.score,
            "notes":       self.notes[:200],
            "found_at":    self.found_at,
            "assigned_to": self.assigned_to,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "JobRecord":
        jr = cls(
            job_id=d.get("job_id", ""),
            platform=d.get("platform", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            budget=float(d.get("budget", 0)),
            skills=d.get("skills", []),
            url=d.get("url", ""),
        )
        jr.status      = d.get("status", "new")
        jr.score       = float(d.get("score", 0))
        jr.notes       = d.get("notes", "")
        jr.found_at    = float(d.get("found_at", time.time()))
        jr.assigned_to = d.get("assigned_to")
        return jr


# ─────────────────────────────────────────────────────────────────────────────
#  JobSearchDB  — lightweight persistence for job records
# ─────────────────────────────────────────────────────────────────────────────
class JobSearchDB:
    _DB_PATH = os.path.join(ROOT_FOLDER, "job_search.db")

    def __init__(self, db_path: str = None):
        self._path = db_path or self._DB_PATH
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _execute(self, sql, params=(), commit=False):
        with self._lock:
            cur = self._conn.execute(sql, params)
            if commit:
                self._conn.commit()
            return cur

    def _create_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id      TEXT PRIMARY KEY,
                platform    TEXT NOT NULL,
                title       TEXT NOT NULL,
                description TEXT,
                budget      REAL DEFAULT 0,
                skills      TEXT DEFAULT '[]',
                url         TEXT DEFAULT '',
                status      TEXT DEFAULT 'new',
                score       REAL DEFAULT 0,
                notes       TEXT DEFAULT '',
                found_at    REAL,
                assigned_to TEXT
            );
            CREATE TABLE IF NOT EXISTS search_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL,
                platform    TEXT    NOT NULL,
                query       TEXT    NOT NULL,
                found_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_score   ON jobs(score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_ts      ON jobs(found_at DESC);
        """)
        self._conn.commit()

    def upsert_job(self, jr: JobRecord):
        self._execute(
            """INSERT INTO jobs
               (job_id,platform,title,description,budget,skills,url,
                status,score,notes,found_at,assigned_to)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                 status=excluded.status, score=excluded.score,
                 notes=excluded.notes, assigned_to=excluded.assigned_to""",
            (jr.job_id, jr.platform, jr.title, jr.description,
             jr.budget, json.dumps(jr.skills), jr.url,
             jr.status, jr.score, jr.notes, jr.found_at, jr.assigned_to),
            commit=True
        )

    def get_jobs(self, status: str = None, limit: int = 50) -> List[Dict]:
        if status:
            rows = self._execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY score DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM jobs ORDER BY found_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["skills"] = json.loads(d.get("skills", "[]"))
            except Exception:
                d["skills"] = []
            result.append(d)
        return result

    def count_by_status(self) -> Dict[str, int]:
        rows = self._execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def log_search(self, platform: str, query: str, found: int):
        self._execute(
            "INSERT INTO search_log (ts, platform, query, found_count) VALUES (?,?,?,?)",
            (time.time(), platform, query, found),
            commit=True
        )

    def job_exists(self, job_id: str) -> bool:
        row = self._execute(
            "SELECT 1 FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return row is not None

    def close(self):
        self._conn.close()


# ─────────────────────────────────────────────────────────────────────────────
#  JobSearchWorker
# ─────────────────────────────────────────────────────────────────────────────
class JobSearchWorker(WorkerAgent):
    """
    Specialized agent: finds and evaluates freelance gigs.

    Key methods
    -----------
    search(platform, skill_tags)   -> List[JobRecord]
    evaluate(job, team_skills)     -> float  (0-1 fit score)
    run_search_cycle()             -> List[JobRecord]  (queued jobs)
    get_queued_jobs(limit)         -> List[Dict]
    update_job_status(id, status)
    get_stats()                    -> Dict
    """

    SEARCH_SYSTEM = (
        "You are a freelance job search expert specializing in AI agent gigs. "
        "Generate realistic, detailed job listings for the given platform and skills. "
        "Return ONLY a JSON array of job objects. "
        "Each job must have: title, description, budget_usd, required_skills, url. "
        "Make budgets realistic ($50-$2000 range). No markdown fences. No other text."
    )

    EVAL_SYSTEM = (
        "You are a senior project manager evaluating freelance job fit. "
        "Given a job listing and the team's skills, score the fit 0.0-1.0 and explain briefly. "
        "Reply ONLY as JSON: {\"score\": 0.75, \"reason\": \"...\", \"assign_to\": \"Coder\"} "
        "assign_to must be one of: Coder, Analyst, Manager, JobSearch, Summarizer. "
        "No markdown fences. No other text."
    )

    # Platform metadata - updated to reflect working platforms
    PLATFORMS = {
        "Reddit":     {"url": "https://reddit.com/r/freelance", "speciality": "freelance tech, content, design"},
        "Fiverr":     {"url": "https://fiverr.com", "speciality": "creative, coding, digital services"},
        "Upwork":     {"url": "https://upwork.com", "speciality": "long-term freelance, professional"},
        "PeerTask":   {"url": "https://peertask.io", "speciality": "coding, data, web scraping"},
        "Social":     {"url": "social platforms", "speciality": "micro-jobs, quick tasks"},
    }

    # Team's known skill set
    TEAM_SKILLS = [
        "Python", "PySide6", "Qt", "SQLite", "LLM integration",
        "automation", "web scraping", "data analysis", "REST APIs",
        "freelance proposal writing", "AI agents", "Ollama",
    ]

    def __init__(self, api_key: str, log_signal, db=None):
        super().__init__(api_key, log_signal, db=db)
        self._job_db  = JobSearchDB()
        self._logger  = AgentLogger(db=db, source="JobSearchWorker",
                                    signal=log_signal)
        self._search_interval = int(os.getenv("JOB_SEARCH_INTERVAL", 300))  # 5 min
        self._last_search = 0.0
        self._platform_idx = 0
        self._new_jobs_callback = None   # set by Manager to get notified

    def set_new_jobs_callback(self, fn):
        self._new_jobs_callback = fn

    # ── Search ────────────────────────────────────────────────────────

    def search(self, platform: str, skill_tags: List[str] = None) -> List[JobRecord]:
        skills = skill_tags or self.TEAM_SKILLS[:6]
        skill_str = ", ".join(skills)
        plat_info = self.PLATFORMS.get(platform, {})

        # All platform discovery uses LLM simulation or social_earning_platform
        prompt = (PromptBuilder()
                  .context(f"Platform: {platform} — {plat_info.get('speciality', '')}")
                  .context(f"Search for jobs requiring: {skill_str}")
                  .instruction("Generate 4-6 realistic job listings as JSON array.")
                  .build())

        self._logger.info(f"Searching {platform} for: {skill_str[:60]}")
        raw = self.llm(system=self.SEARCH_SYSTEM, user=prompt, max_tokens=800)

        jobs: List[JobRecord] = []
        if raw.startswith("ERROR:"):
            self._logger.error(f"LLM search failed: {raw}")
            return jobs

        try:
            import re
            clean = re.sub(r"```[a-z]*|```", "", raw).strip()
            items = json.loads(clean)
            if not isinstance(items, list):
                raise ValueError("Not a list")

            for item in items:
                if not isinstance(item, dict):
                    continue
                jid = fingerprint(platform + item.get("title", "") + str(item.get("budget_usd", 0)))
                if self._job_db.job_exists(jid):
                    continue
                jr = JobRecord(
                    job_id=jid,
                    platform=platform,
                    title=str(item.get("title", "Untitled"))[:120],
                    description=str(item.get("description", ""))[:800],
                    budget=float(item.get("budget_usd", 0)),
                    skills=list(item.get("required_skills", [])),
                    url=str(item.get("url", plat_info.get("url", "")))
                )
                jobs.append(jr)
                self._job_db.upsert_job(jr)

            self._job_db.log_search(platform, skill_str, len(jobs))
            self._logger.info(f"Found {len(jobs)} new jobs on {platform}")
        except Exception as e:
            self._logger.error(f"Parse error: {e} | raw={raw[:100]}")

        return jobs

    # ── Evaluate ──────────────────────────────────────────────────────────────

    def evaluate(self, jr: JobRecord) -> float:
        skill_overlap = len(
            set(s.lower() for s in jr.skills) &
            set(s.lower() for s in self.TEAM_SKILLS)
        )
        # Quick heuristic first (avoids LLM call for obvious mismatches)
        if skill_overlap == 0 and jr.budget < 20:
            jr.score  = 0.05
            jr.status = "rejected"
            jr.notes  = "No skill overlap and low budget"
            self._job_db.upsert_job(jr)
            return jr.score

        prompt = (
            f"Job: {jr.title}\n"
            f"Description: {jr.description[:400]}\n"
            f"Budget: ${jr.budget}\n"
            f"Required skills: {', '.join(jr.skills)}\n\n"
            f"Team skills: {', '.join(self.TEAM_SKILLS)}\n"
            f"Evaluate fit score and suggest assignment."
        )
        raw = self.llm(system=self.EVAL_SYSTEM, user=prompt, max_tokens=200)

        parser = ResponseParser(raw)
        data = parser.json_object()
        if data:
            jr.score       = max(0.0, min(1.0, float(data.get("score", 0.5))))
            jr.notes       = str(data.get("reason", ""))[:200]
            jr.assigned_to = str(data.get("assign_to", ""))
        else:
            # Fallback score based on skill overlap
            jr.score = min(1.0, skill_overlap / max(len(jr.skills), 1) * 0.8)
            jr.notes = f"{skill_overlap} skill(s) matched"

        jr.status = "queued" if jr.score >= 0.45 else "rejected"
        self._job_db.upsert_job(jr)
        self._logger.info(
            f"Evaluated '{jr.title[:40]}' → {jr.score:.2f} "
            f"({'QUEUED' if jr.status == 'queued' else 'rejected'})"
        )
        return jr.score

    # ── Search cycle ──────────────────────────────────────────────────────────

    def run_search_cycle(self) -> List[JobRecord]:
        platforms = list(self.PLATFORMS.keys())
        platform = platforms[self._platform_idx % len(platforms)]
        self._platform_idx += 1

        new_jobs = self.search(platform)
        queued   = []

        for jr in new_jobs:
            jr.status = "evaluating"
            self._job_db.upsert_job(jr)
            score = self.evaluate(jr)
            if jr.status == "queued":
                queued.append(jr)

        self._last_search = time.time()

        if queued and self._new_jobs_callback:
            try:
                self._new_jobs_callback(queued)
            except Exception as e:
                self._logger.warn(f"Callback error: {e}")

        self._logger.info(
            f"Cycle complete: {len(new_jobs)} found, "
            f"{len(queued)} queued from {platform}"
        )
        return queued

    # ── Status ────────────────────────────────────────────────────────────────

    def should_search(self) -> bool:
        return (time.time() - self._last_search) >= self._search_interval

    def get_queued_jobs(self, limit: int = 20) -> List[Dict]:
        return self._job_db.get_jobs(status="queued", limit=limit)

    def get_all_jobs(self, limit: int = 50) -> List[Dict]:
        return self._job_db.get_jobs(limit=limit)

    def update_job_status(self, job_id: str, status: str, notes: str = ""):
        if status not in JOB_STATUSES:
            return
        self._job_db._execute(
            "UPDATE jobs SET status=?, notes=? WHERE job_id=?",
            (status, notes, job_id),
            commit=True
        )

    def get_stats(self) -> Dict:
        counts = self._job_db.count_by_status()
        return {
            "by_status":   counts,
            "total":       sum(counts.values()),
            "queued":      counts.get("queued", 0),
            "applied":     counts.get("applied", 0),
            "won":         counts.get("won", 0),
            "rejected":    counts.get("rejected", 0),
            "last_search": datetime.fromtimestamp(
                self._last_search).strftime("%H:%M:%S")
                if self._last_search else "never",
        }

    def format_job_brief(self, jr_dict: Dict) -> str:
        return (
            f"[{jr_dict['platform']}] {jr_dict['title'][:60]}\n"
            f"  Budget: ${jr_dict['budget']:.0f}  "
            f"Score: {jr_dict['score']:.2f}  "
            f"Skills: {', '.join(jr_dict.get('skills', [])[:4])}\n"
            f"  Notes: {jr_dict.get('notes', '')[:80]}"
        )