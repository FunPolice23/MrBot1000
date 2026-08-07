"""
earning_memory.py — Multi-tiered memory system for MrBot1000.

Tier 1: Opportunity Memory - tracks all discovered opportunities
Tier 2: Outcome Memory - tracks results of executed actions
Tier 3: Skill Memory - learns which jobs/skills are profitable
Tier 4: Reputation Memory - platform-level success/failure rates
Tier 5: Pattern Memory - learns patterns from successes/failures
"""

import os
import json
import sqlite3
import threading
import time
from collections import defaultdict, Counter
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class MemoryEntry:
    """Base memory entry with timestamp."""
    memory_type: str
    key: str
    value: dict
    timestamp: float = field(default_factory=time.time)
    ttl: float = 86400 * 30  # Default 30 days

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class EarningMemory:
    """Multi-tiered memory system for earning pipeline with learning and decay."""

    def __init__(self, db_path: str = None, root_folder: str = None):
        self.db_path = db_path or os.path.join(
            root_folder or os.path.dirname(__file__), "earning_memory.db"
        )
        self._lock = threading.Lock()
        self._init_db()
        
        # In-memory caches for fast access
        self._cache: Dict[str, dict] = {}
        self._stats: Dict[str, dict] = defaultdict(lambda: {
            "success": 0, "failed": 0, "total_revenue": 0.0,
            "last_success": None, "last_failure": None,
            "skills_matched": set(), "skill_count": 0
        })
        
        # Decay parameters
        self._decay_factor = 0.99  # Daily decay for old stats
        self._min_stats = {
            "success": 0, "failed": 0, "total_revenue": 0.0
        }

    def _init_db(self):
        """Initialize the memory database with all tables."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            -- Tier 1: Opportunity Memory
            CREATE TABLE IF NOT EXISTS opportunity_memory (
                key TEXT PRIMARY KEY,
                memory_data TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                created_at REAL,
                updated_at REAL
            );

            -- Tier 2: Outcome Memory
            CREATE TABLE IF NOT EXISTS outcome_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id TEXT NOT NULL,
                action_taken TEXT,
                result TEXT,
                revenue_usd REAL DEFAULT 0,
                time_spent_hours REAL DEFAULT 0,
                was_scam BOOLEAN DEFAULT 0,
                success BOOLEAN DEFAULT 0,
                tags TEXT,
                created_at REAL
            );

            -- Tier 3: Skill Memory
            CREATE TABLE IF NOT EXISTS skill_memory (
                skill TEXT PRIMARY KEY,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                platforms TEXT,
                last_used REAL
            );

            -- Tier 4: Reputation Memory (per platform)
            CREATE TABLE IF NOT EXISTS reputation_memory (
                platform TEXT PRIMARY KEY,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                scam_count INTEGER DEFAULT 0,
                avg_revenue REAL DEFAULT 0,
                total_attempts INTEGER DEFAULT 0,
                last_attempt REAL
            );

            -- Tier 5: Pattern Memory (learning from outcomes)
            CREATE TABLE IF NOT EXISTS pattern_memory (
                pattern_type TEXT,
                pattern_key TEXT,
                success_count INTEGER DEFAULT 0,
                failure_count INTEGER DEFAULT 0,
                total_revenue REAL DEFAULT 0,
                confidence REAL DEFAULT 0.0,
                last_seen REAL,
                PRIMARY KEY (pattern_type, pattern_key)
            );

            -- Learning History
            CREATE TABLE IF NOT EXISTS learning_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learning_type TEXT NOT NULL,
                insight TEXT,
                confidence REAL,
                created_at REAL
            );

            -- Decay tracking
            CREATE TABLE IF NOT EXISTS decay_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT,
                entity_key TEXT,
                old_value REAL,
                new_value REAL,
                decay_factor REAL,
                applied_at REAL
            );

            -- Create indexes
            CREATE INDEX IF NOT EXISTS idx_opp_type ON opportunity_memory(memory_type);
            CREATE INDEX IF NOT EXISTS idx_outcome_tags ON outcome_memory(tags);
            CREATE INDEX IF NOT EXISTS idx_skill_platforms ON skill_memory(platforms);
            CREATE INDEX IF NOT EXISTS idx_pattern_type ON pattern_memory(pattern_type);
            CREATE INDEX IF NOT EXISTS idx_decay_entity ON decay_log(entity_type, entity_key);
        """)
        conn.commit()
        conn.close()

    # ── Decay System ──────────────────────────────────────────────

    def apply_decay(self, entity_type: str, entity_key: str, 
                    factor: float = 0.95) -> float:
        """Apply decay to stats. Returns new value."""
        new_value = self._apply_decay_to_stats(entity_type, entity_key, factor)
        
        # Log decay
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO decay_log (entity_type, entity_key, old_value, new_value, decay_factor, applied_at) VALUES (?, ?, ?, ?, ?, ?)",
                (entity_type, entity_key, 0, new_value, factor, time.time())
            )
            conn.commit()
            conn.close()
        
        return new_value

    def _apply_decay_to_stats(self, entity_type: str, entity_key: str, factor: float) -> float:
        """Apply decay to a specific stat."""
        # For simplicity, return 0 - actual implementation would update DB
        return 0.0

    def periodic_decay(self, decay_factor: float = 0.99):
        """Apply periodic decay to all stats."""
        # Update in-memory stats
        for key in self._stats:
            self._stats[key]["total_revenue"] *= decay_factor
            if self._stats[key]["success"] < 5:  # Decay near-zero stats faster
                self._stats[key]["total_revenue"] *= 0.95

    # ── Tier 1: Opportunity Memory ──────────────────────────────────

    def remember_opportunity(self, opp_id: str, memory_data: dict, opp_type: str):
        """Store an opportunity in memory."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT OR REPLACE INTO opportunity_memory
                   (key, memory_data, memory_type, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (opp_id, json.dumps(memory_data), opp_type,
                 time.time(), time.time())
            )
            conn.commit()
            conn.close()

        self._cache[f"opp_{opp_id}"] = memory_data

    def get_opportunity_history(self, opp_id: str) -> Optional[dict]:
        """Get history for a specific opportunity."""
        if f"opp_{opp_id}" in self._cache:
            return self._cache[f"opp_{opp_id}"]

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT memory_data, memory_type FROM opportunity_memory WHERE key=?",
                (opp_id,)
            ).fetchone()
            conn.close()

        if row:
            data = json.loads(row[0])
            self._cache[f"opp_{opp_id}"] = data
            return data
        return None

    def get_opportunities_by_type(self, opp_type: str, limit: int = 50) -> List[dict]:
        """Get all opportunities of a specific type."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT key, memory_data FROM opportunity_memory "
                "WHERE memory_type=? LIMIT ?",
                (opp_type, limit)
            ).fetchall()
            conn.close()

        return [json.loads(r[1]) for r in rows]

    # ── Tier 2: Outcome Memory ─────────────────────────────────────

    def record_outcome(self, opp_id: str, action: str, result: str,
                       revenue_usd: float = 0, time_spent: float = 0,
                       success: bool = True, tags: List[str] = None,
                       was_scam: bool = False):
        """Record an outcome from executing an action."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """INSERT INTO outcome_memory
                   (opportunity_id, action_taken, result, revenue_usd,
                    time_spent_hours, was_scam, success, tags, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (opp_id, action, result, revenue_usd, time_spent,
                 was_scam, success, json.dumps(tags or []), time.time())
            )
            conn.commit()
            conn.close()

        # Extract patterns from this outcome
        self._extract_patterns(opp_id, action, result, success, tags, revenue_usd)

        # Update stats
        self._update_stats_from_outcome(opp_id, success, revenue_usd, tags)

    def get_outcome_history(self, opp_id: str) -> List[dict]:
        """Get all outcomes for an opportunity."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT action_taken, result, revenue_usd, time_spent_hours, "
                "was_scam, success, tags, created_at "
                "FROM outcome_memory WHERE opportunity_id=?",
                (opp_id,)
            ).fetchall()
            conn.close()

        keys = ["action", "result", "revenue", "time_spent", "scam", "success", "tags", "ts"]
        return [dict(zip(keys, row)) for row in rows]

    def get_recent_outcomes(self, limit: int = 20) -> List[dict]:
        """Get most recent outcomes."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT opportunity_id, action_taken, result, revenue_usd, "
                "success, created_at FROM outcome_memory "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            conn.close()

        keys = ["opp_id", "action", "result", "revenue", "success", "ts"]
        return [dict(zip(keys, row)) for row in rows]

    # ── Tier 3: Skill Memory ───────────────────────────────────────

    def remember_success(self, skill: str, platform: str, revenue: float):
        """Remember that a skill was successful on a platform."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT success_count, total_revenue, platforms FROM skill_memory WHERE skill=?",
                (skill,)
            ).fetchone()

            now = time.time()
            if row is None:
                platforms = [platform]
                conn.execute(
                    """INSERT INTO skill_memory
                       (skill, success_count, failed_count, total_revenue, platforms, last_used)
                       VALUES (?, 1, 0, ?, ?, ?)""",
                    (skill, revenue, json.dumps(platforms), now)
                )
            else:
                success_count = int(row[0] or 0) + 1
                total_revenue = float(row[1] or 0.0) + revenue
                existing_platforms_raw = row[2]
                try:
                    existing_platforms = json.loads(existing_platforms_raw) if existing_platforms_raw else []
                except (TypeError, json.JSONDecodeError):
                    existing_platforms = []

                if not isinstance(existing_platforms, list):
                    existing_platforms = [str(existing_platforms)]
                if platform not in existing_platforms:
                    existing_platforms.append(platform)

                conn.execute(
                    """UPDATE skill_memory
                       SET success_count=?, total_revenue=?, platforms=?, last_used=?
                       WHERE skill=?""",
                    (success_count, total_revenue, json.dumps(existing_platforms), now, skill)
                )
            conn.commit()
            conn.close()

        self._stats[skill]["success"] += 1
        self._stats[skill]["total_revenue"] += revenue

    def get_successful_skills(self, min_success: int = 1, limit: int = 20) -> List[dict]:
        """Get skills that have been successful."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT skill, success_count, failed_count, total_revenue, platforms "
                "FROM skill_memory WHERE success_count >= ? ORDER BY total_revenue DESC LIMIT ?",
                (min_success, limit)
            ).fetchall()
            conn.close()

        keys = ["skill", "success", "failed", "revenue", "platforms"]
        result = []
        for row in rows:
            d = dict(zip(keys, row))
            d["platforms"] = json.loads(d["platforms"]) if isinstance(d["platforms"], str) else d["platforms"]
            result.append(d)
        return result

    # ── Tier 4: Reputation Memory ─────────────────────────────────

    def update_reputation(self, platform: str, success: bool, revenue: float = 0):
        """Update platform reputation."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            if success:
                conn.execute(
                    """INSERT INTO reputation_memory (platform, success_count, avg_revenue, total_attempts, last_attempt)
                       VALUES (?, 1, ?, 1, ?)
                       ON CONFLICT(platform) DO UPDATE SET
                         success_count = success_count + 1,
                         total_attempts = total_attempts + 1,
                         avg_revenue = (avg_revenue * (total_attempts - 1) + ?) / total_attempts,
                         last_attempt = ?""",
                    (platform, revenue, time.time(), revenue, time.time())
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO reputation_memory (platform) VALUES (?)""",
                    (platform,)
                )
                conn.execute(
                    """UPDATE reputation_memory SET failed_count = failed_count + 1,
                       total_attempts = total_attempts + 1, last_attempt = ?
                       WHERE platform = ?""",
                    (time.time(), platform)
                )
            conn.commit()
            conn.close()

    def get_platform_reputation(self, platform: str) -> dict:
        """Get reputation metrics for a platform."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT success_count, failed_count, scam_count, avg_revenue, total_attempts "
                "FROM reputation_memory WHERE platform=?",
                (platform,)
            ).fetchone()
            conn.close()

        if row:
            total = row[4]
            return {
                "platform": platform,
                "success": row[0],
                "failed": row[1],
                "scam": row[2],
                "avg_revenue": row[3],
                "total": total,
                "success_rate": row[0] / total if total else 0
            }
        return {"platform": platform, "success": 0, "failed": 0, "scam": 0, "avg_revenue": 0, "total": 0, "success_rate": 0}

    def get_all_reputations(self) -> List[dict]:
        """Get reputation for all platforms."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT platform, success_count, failed_count, scam_count, avg_revenue, total_attempts "
                "FROM reputation_memory ORDER BY total_attempts DESC"
            ).fetchall()
            conn.close()

        results = []
        for row in rows:
            total = row[5]
            success_rate = row[1] / total if total else 0
            results.append({
                "platform": row[0],
                "success": row[1],
                "failed": row[2],
                "scam": row[3],
                "avg_revenue": row[4],
                "total": total,
                "success_rate": success_rate
            })
        return results

    # ── Tier 5: Pattern Memory (Learning) ─────────────────────────

    def _extract_patterns(self, opp_id: str, action: str, result: str,
                          success: bool, tags: List[str], revenue: float):
        """Extract learning patterns from outcomes."""
        # Pattern: action type -> success
        self._store_pattern("action_success", action, success, revenue)
        
        # Pattern: tag combinations
        if tags:
            for tag in tags:
                self._store_pattern("tag_success", tag, success, revenue)
        
        # Pattern: platform + action combination
        opp_mem = self.get_opportunity_history(opp_id)
        if opp_mem:
            platform = opp_mem.get("platform", "unknown")
            self._store_pattern("platform_action", f"{platform}_{action}", success, revenue)

    def _store_pattern(self, pattern_type: str, pattern_key: str, 
                       success: bool, revenue: float):
        """Store a pattern in memory."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            if success:
                conn.execute(
                    """INSERT INTO pattern_memory 
                       (pattern_type, pattern_key, success_count, total_revenue, confidence, last_seen)
                       VALUES (?, ?, 1, ?, 0.8, ?)
                       ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET
                         success_count = success_count + 1,
                         total_revenue = total_revenue + ?,
                         confidence = MIN(1.0, confidence + 0.1),
                         last_seen = ?""",
                    (pattern_type, pattern_key, revenue, time.time(), revenue, time.time())
                )
            else:
                conn.execute(
                    """INSERT INTO pattern_memory 
                       (pattern_type, pattern_key, failure_count, confidence, last_seen)
                       VALUES (?, ?, 1, 0.1, ?)
                       ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET
                         failure_count = failure_count + 1,
                         confidence = MAX(0.0, confidence - 0.05),
                         last_seen = ?""",
                    (pattern_type, pattern_key, time.time(), time.time())
                )
            conn.commit()
            conn.close()

    def get_pattern_confidence(self, pattern_type: str, pattern_key: str) -> float:
        """Get confidence score for a pattern."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT confidence, success_count, failure_count FROM pattern_memory WHERE pattern_type=? AND pattern_key=?",
                (pattern_type, pattern_key)
            ).fetchone()
            conn.close()

        if row and row[0] > 0:
            return row[0]
        return 0.0

    def get_successful_patterns(self, pattern_type: str, min_confidence: float = 0.5) -> List[dict]:
        """Get patterns that have been successful."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT pattern_key, success_count, failure_count, total_revenue, confidence "
                "FROM pattern_memory WHERE pattern_type=? AND confidence >= ? ORDER BY confidence DESC",
                (pattern_type, min_confidence)
            ).fetchall()
            conn.close()

        keys = ["pattern", "success", "failed", "revenue", "confidence"]
        return [dict(zip(keys, row)) for row in rows]

    def find_similar_opportunities(self, opp: dict, limit: int = 10) -> List[dict]:
        """Find similar opportunities based on learned patterns."""
        results = []
        
        # Search by platform
        platform = opp.get("platform", "")
        if platform:
            patterns = self.get_successful_patterns(f"platform_action", 0.3)
            for p in patterns[:limit]:
                results.append({"pattern": platform, "confidence": p["confidence"]})
        
        # Search by skills in description
        desc = opp.get("description", "").lower()
        skills = ["python", "ai", "data", "code", "write", "review"]
        matching_skills = [s for s in skills if s in desc]
        
        for skill in matching_skills:
            patterns = self.get_successful_patterns("tag_success", 0.3)
            for p in patterns[:limit]:
                results.append({"skill": skill, "confidence": p["confidence"]})
        
        return sorted(results, key=lambda x: x.get("confidence", 0), reverse=True)

    # ── Learning ───────────────────────────────────────────────────

    def store_learning(self, learning_type: str, insight: str, confidence: float = 0.5):
        """Store a learned insight."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO learning_history (learning_type, insight, confidence, created_at) "
                "VALUES (?, ?, ?, ?)",
                (learning_type, insight, confidence, time.time())
            )
            conn.commit()
            conn.close()

    def get_recent_learned(self, learning_type: str = None, limit: int = 10) -> List[dict]:
        """Get recent learned insights."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            if learning_type:
                rows = conn.execute(
                    "SELECT learning_type, insight, confidence, created_at "
                    "FROM learning_history WHERE learning_type=? ORDER BY created_at DESC LIMIT ?",
                    (learning_type, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT learning_type, insight, confidence, created_at "
                    "FROM learning_history ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            conn.close()

        keys = ["type", "insight", "confidence", "ts"]
        return [dict(zip(keys, row)) for row in rows]

    # ── Stats helpers ─────────────────────────────────────────────

    def _update_stats_from_outcome(self, opp_id: str, success: bool, revenue: float, tags):
        """Update in-memory stats from an outcome."""
        tag_list = tags or []
        for tag in tag_list:
            if success:
                self._stats[f"tag_{tag}"]["success"] += 1
                self._stats[f"tag_{tag}"]["total_revenue"] += revenue
            else:
                self._stats[f"tag_{tag}"]["failed"] += 1

    def get_memory_summary(self) -> dict:
        """Get a summary of all memory."""
        self.periodic_decay()  # Apply decay on summary
        
        total_opps = len([k for k in self._cache if k.startswith("opp_")])
        
        return {
            "memory_stats": {
                "cached_opportunities": total_opps,
                "tracked_skills": len([k for k in self._stats if "tag_" not in k]),
            },
            "top_platforms": self.get_all_reputations()[:10],
            "recent_learned": self.get_recent_learned(limit=5),
            "successful_skills": self.get_successful_skills(min_success=1),
            "successful_patterns": {
                "actions": self.get_successful_patterns("action_success"),
                "tags": self.get_successful_patterns("tag_success"),
            }
        }

    def clear_expired(self):
        """Clear expired memory entries."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "DELETE FROM opportunity_memory WHERE updated_at < ?",
                (time.time() - 86400 * 30,)  # 30 days
            )
            conn.commit()
            conn.close()


# Convenience function
def create_memory(db_path: str = None) -> EarningMemory:
    """Factory function to create an EarningMemory instance."""
    return EarningMemory(db_path=db_path)


# Quick test
if __name__ == "__main__":
    import tempfile, os
    
    tmp_db = tempfile.mktemp(suffix=".db")
    mem = EarningMemory(db_path=tmp_db)
    
    # Test opportunity memory
    mem.remember_opportunity("test_123", {"title": "Test Gig", "value": 100}, "gig")
    print(f"Stored opportunity: {mem.get_opportunity_history('test_123')}")
    
    # Test outcome memory
    mem.record_outcome("test_123", "apply", "Applied successfully", 100, 0.5, True, ["clawgig"])
    print("Recorded outcome")
    
    # Test skill memory
    mem.remember_success("python", "ClawGig", 100)
    print(f"Skills: {mem.get_successful_skills()}")
    
    # Test reputation
    mem.update_reputation("ClawGig", True, 100)
    print(f"Reputation: {mem.get_platform_reputation('ClawGig')}")
    
    # Test patterns
    patterns = mem.get_successful_patterns("action_success")
    print(f"Patterns: {len(patterns)} learned")
    
    # Test memory summary
    summary = mem.get_memory_summary()
    print(f"Summary: {summary['memory_stats']}")
    
    os.unlink(tmp_db)
    print("Memory system test: PASSED")