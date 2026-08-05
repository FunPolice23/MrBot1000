"""
earning_pipeline.py — Earning pipeline engine for MrBot1000.

Replaces agent-based orchestration with a direct, efficient pipeline:
  1. DISCOVER → Scan for opportunities (gigs, airdrops, DeFi, content)
  2. EVALUATE → Score and rank opportunities using Ollama
  3. FILTER → Apply user preferences and risk limits
  4. EXECUTE → Take safe automated actions
  5. TRACK → Log outcomes and update reputation
"""

import os
import time
import json
import re
import sqlite3
import threading
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field

from agents.social_earning_platform import SocialEarningPlatform, SocialOpportunity
from agents.document_scanner import DocumentScanner, QualityController
from agents.airdrop_scanner import AirdropScanner
from agents.defi_scanner import DeFiScanner
from agents.microtask_client import MicrotaskClient, MicrotaskGig
from agents.fiverr_client import FiverrClient, FiverrGig
from agents.earning_discoverer import EarningDiscoverer, EarningOpportunity
from earning_memory import EarningMemory

ROOT_FOLDER = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_FOLDER, "earning.db")
MEMORY_PATH = os.path.join(ROOT_FOLDER, "earning_memory.db")


@dataclass
class Opportunity:
    id: str
    source: str = ""
    type: str = ""
    title: str = ""
    description: str = ""
    platform: str = ""
    payment_type: str = "usd"
    payment_amount: float = 0.0
    payment_currency: str = "USD"
    estimated_usd_value: float = 0.0
    min_amount: float = 1.0  # Minimum expected payout
    time_required_hours: float = 0.0
    risk_level: str = "low"
    skill_match: float = 0.0
    effort_score: float = 0.5
    urgency: float = 0.0
    url: str = ""
    status: str = "new"
    found_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    outcome: str = ""
    history: List[Dict] = field(default_factory=list)


@dataclass
class PipelineResult:
    success: bool
    opportunity: Optional[Opportunity] = None
    action_taken: str = ""
    message: str = ""
    timestamp: float = field(default_factory=time.time)


class EarningPipeline:
    """Main earning pipeline engine with integrated memory system."""

    def __init__(self, db_path: str = DB_PATH,
                       memory_path: str = None,
                       log_fn: Callable = None):
        self.db_path = db_path
        self.memory_path = memory_path or MEMORY_PATH
        self._log = log_fn or print
        self._lock = threading.Lock()
        self._init_db()
        
        # Initialize memory system
        self.memory = EarningMemory(db_path=self.memory_path)

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT,
                description TEXT,
                platform TEXT,
                payment_type TEXT DEFAULT 'usd',
                payment_amount REAL DEFAULT 0,
                payment_currency TEXT DEFAULT 'USD',
                estimated_usd_value REAL DEFAULT 0,
                time_required_hours REAL DEFAULT 0,
                risk_level TEXT DEFAULT 'low',
                skill_match REAL DEFAULT 0,
                effort_score REAL DEFAULT 0.5,
                urgency REAL DEFAULT 0,
                url TEXT,
                status TEXT DEFAULT 'new',
                found_at REAL,
                completed_at REAL DEFAULT 0,
                outcome TEXT
            );
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                opportunity_id TEXT,
                action TEXT,
                result TEXT,
                revenue_usd REAL DEFAULT 0,
                revenue_currency TEXT DEFAULT 'USD',
                time_spent_hours REAL DEFAULT 0,
                was_scam BOOLEAN DEFAULT 0
            );
        """)
        conn.commit()
        conn.close()

    def _db_execute(self, sql, params=(), commit=False):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(sql, params)
            if commit:
                conn.commit()
            conn.close()
            return cursor

    # ── Stage 1: DISCOVER ──────────────────────────────────

    def discover(self, sources: List[str] = None
                  ) -> List[Opportunity]:
        """Run all discovery sources and return raw opportunities."""
        sources = sources or [
            "social", "upwork", "fiverr", "airdrop",
            "defi", "microtask", "content", "dynamic",
        ]
        all_opps = []

        for source in sources:
            try:
                opps = self._discover_source(source)
                
                # Store in memory
                for opp in opps:
                    self.memory.remember_opportunity(opp.id, {
                        "title": opp.title,
                        "description": opp.description,
                        "platform": opp.platform,
                        "type": opp.type,
                        "source": opp.source,
                        "min_amount": opp.min_amount,
                    }, opp.type)
                
                all_opps.extend(opps)
                self._log(f"[Discover] Found {len(opps)} from {source}")
            except Exception as e:
                self._log(f"[Discover] Error from {source}: {e}")
            time.sleep(1)

        return all_opps

    def _discover_source(self, source: str) -> List[Opportunity]:
        """SECURE: Only valid sources allowed - no dead/external APIs."""
        valid_sources = {
            "social", "upwork", "fiverr", "airdrop",
            "defi", "microtask", "content", "dynamic"
        }
        if source not in valid_sources:
            self._log(f"[Discover] Invalid source '{source}' blocked")
            return []
        
        if source == "social":
            return self._discover_social()
        elif source == "upwork":
            return self._discover_upwork()
        elif source == "fiverr":
            return self._discover_fiverr()
        elif source == "airdrop":
            return self._discover_airdrops()
        elif source == "defi":
            return self._discover_defi()
        elif source == "microtask":
            return self._discover_microtasks()
        elif source == "content":
            return self._discover_content()
        elif source == "dynamic":
            return self._discover_dynamic()
        return []

    def _discover_upwork(self) -> List[Opportunity]:
        client_id = os.getenv("UPWORK_CLIENT_ID", "")
        client_secret = os.getenv("UPWORK_CLIENT_SECRET", "")
        access_token = os.getenv("UPWORK_ACCESS_TOKEN", "")
        refresh_token = os.getenv("UPWORK_REFRESH_TOKEN", "")

        if not all([client_id, client_secret, access_token,
                    refresh_token]):
            return []

        from agents.upwork_client import UpworkClient
        client = UpworkClient(
            client_id, client_secret,
            access_token, refresh_token,
        )
        gigs = client.find_gigs(q="python", limit=20)

        return [
            Opportunity(
                id=f"upwork_{gig.id}",
                source="upwork",
                type="gig",
                title=gig.title,
                description=gig.description,
                platform="Upwork",
                payment_type="usd",
                estimated_usd_value=gig.budget_usd,
                min_amount=gig.budget_usd,
                url=gig.url,
                found_at=time.time(),
            )
            for gig in gigs
        ]

    def _discover_fiverr(self) -> List[Opportunity]:
        client = FiverrClient()
        gigs = client.find_gigs(query="python", limit=20)

        return [
            Opportunity(
                id=f"fiverr_{gig.id}",
                source="fiverr",
                type="gig",
                title=gig.title,
                description=gig.description,
                platform="Fiverr",
                payment_type="usd",
                estimated_usd_value=gig.budget_usd,
                min_amount=gig.budget_usd,
                url=gig.url,
                found_at=time.time(),
            )
            for gig in gigs
        ]

    
    def _discover_social(self) -> List[Opportunity]:
        """Discover opportunities from social platforms (Reddit, Twitter, LinkedIn, etc.)."""
        platform = SocialEarningPlatform()
        raw_opps = platform.discover_all()
        
        return [
            Opportunity(
                id=f"social_{opp.id}",
                source="social",
                type=opp.type if opp.type else "gig",
                title=opp.title,
                description=opp.description,
                platform=opp.platform,
                payment_type=opp.payment_type,
                estimated_usd_value=opp.estimated_value,
                min_amount=opp.min_amount,
                risk_level=opp.risk_level.lower() if opp.risk_level else "low",
                url=opp.url,
                found_at=opp.posted_at if opp.posted_at else time.time(),
            )
            for opp in raw_opps
        ]

    def _discover_airdrops(self) -> List[Opportunity]:
        scanner = AirdropScanner()
        airdrops = scanner.scan_feeds()

        return [
            Opportunity(
                id=f"airdrop_{a.id}",
                source="airdrop",
                type="airdrop",
                title=a.title,
                description=a.description,
                platform=a.platform,
                payment_type="crypto",
                payment_currency=a.token_symbol or "TOKEN",
                estimated_usd_value=a.estimated_value_usd,
                min_amount=a.estimated_value_usd,
                risk_level=a.risk_level,
                url=a.url,
                found_at=time.time(),
            )
            for a in airdrops
        ]

    def _discover_defi(self) -> List[Opportunity]:
        scanner = DeFiScanner()
        opps = scanner.scan_all()

        return [
            Opportunity(
                id=f"defi_{opp.id}",
                source="defi",
                type=opp.type,
                title=f"{opp.protocol} {opp.type} — {opp.token_symbol}",
                description=(
                    f"{opp.protocol} {opp.type} "
                    f"with {opp.apy_percent}% APY"
                ),
                platform=opp.protocol,
                payment_type="crypto",
                payment_currency=opp.token_symbol,
                estimated_usd_value=opp.tvl_usd
                * (opp.apy_percent / 100) * 0.01,
                min_amount=0.01,
                risk_level=opp.risk_level,
                url=opp.url,
                found_at=time.time(),
            )
            for opp in opps
        ]

    def _discover_microtasks(self) -> List[Opportunity]:
        client = MicrotaskClient()
        gigs = client.find_gigs(platform="all", query="ai ml data")

        return [
            Opportunity(
                id=f"microtask_{gig.id}",
                source="microtask",
                type="microtask",
                title=gig.title,
                description=gig.description,
                platform=gig.platform,
                payment_type="usd",
                estimated_usd_value=gig.payout_usd,
                min_amount=gig.payout_usd,
                url=gig.url,
                found_at=time.time(),
            )
            for gig in gigs
        ]

    def _discover_content(self) -> List[Opportunity]:
        return [
            Opportunity(
                id=f"content_{i}",
                source="content",
                type="content",
                title=f"Write for {plat['platform']}",
                description=f"Create content on {plat['platform']}",
                platform=plat["platform"],
                payment_type="crypto",
                estimated_usd_value=0,
                min_amount=5.0,  # Micro-payment baseline
                risk_level="low",
                url=f"https://{plat['platform'].replace('.', '')}",
                found_at=time.time(),
            )
            for i, plat in enumerate([
                {"platform": "Mirror.xyz", "type": "blog",
                 "pay": "crypto",
                 "description": "Publish articles and earn crypto"},
                {"platform": "Hive", "type": "blog",
                 "pay": "crypto",
                 "description": "Write and earn HIVE tokens"},
                {"platform": "Gitcoin", "type": "github",
                 "pay": "crypto",
                 "description": "Contribute to open source and "
                               "earn grants"},
            ])
        ]

    def _discover_dynamic(self) -> List[Opportunity]:
        discoverer = EarningDiscoverer()
        raw_opps = discoverer.discover_all()

        return [
            Opportunity(
                id=opp.id,
                source=opp.source,
                type="referral" if "referral" in opp.platform.lower()
                       else ("faucet" if "faucet" in opp.title.lower()
                       else "airdrop"),
                title=opp.title,
                description=opp.description,
                platform=opp.platform,
                payment_type="usd" if opp.payment_type == "usd"
                             else "crypto",
                estimated_usd_value=opp.min_amount,
                min_amount=opp.min_amount,
                risk_level=opp.risk_level,
                url=opp.url,
                found_at=time.time(),
            )
            for opp in raw_opps
        ]

    # ── Stage 2: EVALUATE ──────────────────────────────────

    def evaluate(self, opportunities: List[Opportunity]
                 ) -> List[Opportunity]:
        """Score opportunities using Ollama or heuristics."""
        evaluated = []

        for opp in opportunities:
            try:
                scored = self._evaluate_opportunity(opp)
                evaluated.append(scored)
            except Exception as e:
                self._log(
                    f"[Evaluate] Error evaluating {opp.id}: {e}"
                )
                opp.status = "evaluated"
                evaluated.append(opp)
            time.sleep(0.5)

        return evaluated

    def _evaluate_opportunity(self, opp: Opportunity
                              ) -> Opportunity:
        import httpx

        # Check memory first for known patterns
        memory = self.memory.get_opportunity_history(opp.id)
        if memory and "past_decisions" in memory:
            opp.skill_match = memory.get("skill_match", opp.skill_match)

        prompt = (
            f"Evaluate this earning opportunity:\n"
            f"Title: {opp.title}\n"
            f"Description: {opp.description[:300]}\n"
            f"Platform: {opp.platform}\n"
            f"Payment: {opp.payment_amount} {opp.payment_currency}\n"
            f"Min Amount: ${opp.min_amount}\n\n"
            f"Score each axis 0-10:\n"
            f"1. Profit potential\n"
            f"2. Effort (lower is better)\n"
            f"3. Risk (scam likelihood)\n"
            f"4. Urgency\n"
            f"5. Skill match\n\n"
            f'Reply ONLY as JSON: {{"profit": N, "effort": N, '
            f'"risk": N, "urgency": N, "skill_match": N}}'
        )

        try:
            model = os.getenv("OLLAMA_MODEL", "llama3.2")
            response = httpx.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system",
                         "content": (
                             "You are an expert evaluator of earning "
                             "opportunities. Be honest. Flag scams."
                         )},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"num_predict": 300},
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("message", {}).get("content", "")

            json_match = re.search(
                r"\{.*\}", content, re.DOTALL,
            )
            if json_match:
                scores = json.loads(json_match.group())
                opp.skill_match = scores.get("skill_match", 0.5)
                opp.effort_score = scores.get("effort", 0.5)
                risk = scores.get("risk", 5)
                opp.risk_level = (
                    "high" if risk >= 7
                    else ("medium" if risk >= 4 else "low")
                )
                opp.urgency = scores.get("urgency", 0.0)

                if scores.get("scam_prob", 0.0) > 0.7:
                    opp.risk_level = "high"
                    opp.status = "rejected"

        except Exception as e:
            self._log(f"[Evaluate] LLM error for {opp.id}: {e}")
            # Apply heuristic fallback with memory context
            title_desc = (opp.title + " " + opp.description).lower()
            if opp.source == "content":
                base_skill = 0.5
            elif opp.source in ["referral", "airdrop"]:
                base_skill = 0.6
            else:
                base_skill = 0.2

            skills = ["python", "ai", "data", "write", "content", "code", "dev", "review", "test"]
            matched = sum(1 for s in skills if s in title_desc)
            opp.skill_match = min(1.0, base_skill + (matched / len(skills)) * 0.5)
            opp.effort_score = 0.5
            opp.risk_level = "low" if "airdrop" not in opp.type else "medium"
            opp.urgency = 0.3
            opp.status = "evaluated"

        return opp

    # ── Stage 3: FILTER ────────────────────────────────────

    def filter(self, opportunities: List[Opportunity],
                 max_risk: str = "medium",
                 min_skill_match: float = 0.3,
                 min_usd_value: float = 0.0,
                 payment_types: List[str] = None
                 ) -> List[Opportunity]:
        payment_types = payment_types or [
            "usd", "crypto", "token",
        ]
        risk_order = {"low": 0, "medium": 1, "high": 2}
        max_risk_val = risk_order.get(max_risk, 1)

        # Get platform reputations for intelligent filtering
        platform_reps = {}
        for opp in opportunities:
            rep = self.memory.get_platform_reputation(opp.platform)
            platform_reps[opp.platform] = rep

        filtered = []
        for opp in opportunities:
            if opp.payment_type not in payment_types:
                continue
            if risk_order.get(opp.risk_level, 2) > max_risk_val:
                continue
            if opp.skill_match < min_skill_match:
                continue
            if opp.estimated_usd_value < min_usd_value:
                continue
            
            # Apply memory-based boosts and penalties
            self._apply_memory_boost(opp, platform_reps)
            
            filtered.append(opp)

        # Sort by value * skill_match / (effort + 0.1)
        filtered.sort(
            key=lambda o: (
                o.estimated_usd_value * o.skill_match
            ) / (o.effort_score + 0.1),
            reverse=True,
        )

        return filtered


    def _apply_memory_boost(self, opp, 
                            platform_reps):
        """Apply memory-based boosts and penalties to opportunity scoring."""
        # Platform reputation boost
        rep = platform_reps.get(opp.platform, {})
        success_rate = rep.get('success_rate', 0)
        if success_rate > 0.8:
            # Boost skill match for trusted platforms
            opp.skill_match = min(1.0, opp.skill_match + 0.15)
            # Reduce effort score
            opp.effort_score = max(0.1, opp.effort_score * 0.7)
        elif success_rate < 0.3 and success_rate > 0:
            # Penalize untrustworthy platforms
            opp.skill_match = max(0.0, opp.skill_match - 0.2)
            opp.risk_level = 'high'

        # Pattern-based boost for known good actions
        pattern_conf = self.memory.get_pattern_confidence(
            f'platform_action', f'{opp.platform}_execute'
        )
        if pattern_conf > 0.7:
            opp.skill_match = min(1.0, opp.skill_match + 0.1)

    # ── Stage 4: EXECUTE ───────────────────────────────────

    def execute(self, opportunity: Opportunity
                ) -> PipelineResult:
        """Execute an opportunity action."""
        if opportunity.source == "airdrop":
            return self._execute_airdrop(opportunity)
        elif opportunity.source == "defi":
            return self._execute_defi(opportunity)
        elif opportunity.source == "microtask":
            return self._execute_microtask(opportunity)
        elif opportunity.source in ["referral", "faucet"]:
            return self._execute_simple(opportunity)
        else:
            return PipelineResult(
                success=False,
                opportunity=opportunity,
                action_taken="none",
                message=f"No executor for {opportunity.source}",
            )

    def _execute_airdrop(self, opp: Opportunity) -> PipelineResult:
        try:
            claimer = AirdropClaimer()
            result = claimer.claim(opp.url)
            if result.success:
                opp.status = "claimed"
                opp.outcome = "claimed"
                self.memory.update_reputation(opp.platform, True)
            else:
                opp.status = "claim_failed"
                opp.outcome = result.message
                self.memory.update_reputation(opp.platform, False)
            
            self.memory.record_outcome(opp.id, "claim", result.message,
                                       opp.estimated_usd_value, 0.1, result.success)
            return PipelineResult(
                success=result.success,
                opportunity=opp,
                action_taken="claim",
                message=result.message,
            )
        except Exception as e:
            return PipelineResult(
                success=False,
                opportunity=opp,
                action_taken="claim",
                message=f"Claim failed: {e}",
            )

    def _execute_defi(self, opp: Opportunity) -> PipelineResult:
        return PipelineResult(
            success=False,
            opportunity=opp,
            action_taken="none",
            message="DeFi execution requires wallet setup - not implemented",
        )

    def _execute_microtask(self, opp: Opportunity) -> PipelineResult:
        return PipelineResult(
            success=False,
            opportunity=opp,
            action_taken="none",
            message="Microtask execution requires platform API keys",
        )

    def _execute_simple(self, opp: Opportunity) -> PipelineResult:
        """Execute simple opportunities like referrals and faucets."""
        self.memory.update_reputation(opp.platform, True)
        self.memory.record_outcome(opp.id, opp.status or "execute", "Action available",
                                   opp.min_amount * 0.5, 0.1, True, [opp.source])
        return PipelineResult(
            success=True,
            opportunity=opp,
            action_taken=opp.status or "execute",
            message=f"Action available: {opp.url}",
        )

    # ── Stage 5: TRACK ─────────────────────────────────────

    def get_revenue_report(self, days: int = 30) -> dict:
        since = time.time() - (days * 86400)
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT SUM(revenue_usd), COUNT(*), "
                "SUM(time_spent_hours) FROM outcomes WHERE ts > ?",
                (since,),
            )
            row = cursor.fetchone()
            conn.close()

        return {
            "total_revenue_usd": row[0] or 0.0,
            "total_outcomes": row[1] or 0,
            "total_time_hours": row[2] or 0.0,
            "avg_revenue_per_outcome": (
                row[0] / row[1] if row[1] and row[0] else 0.0
            ),
            "period_days": days,
        }

    def get_memory_summary(self) -> dict:
        """Get memory system summary for dashboard."""
        return self.memory.get_memory_summary()

    def get_platform_reputation(self, platform: str) -> dict:
        """Get reputation for a specific platform."""
        return self.memory.get_platform_reputation(platform)

    def get_successful_skills(self, min_success: int = 1) -> List[dict]:
        """Get skills that have been successful."""
        return self.memory.get_successful_skills(min_success=min_success)

    # ── Full cycle ───────────────────────────────────────────

    def run_full_cycle(self, sources: List[str] = None,
                       max_risk: str = "medium") -> PipelineResult:
        """Run complete discovery → evaluate → filter → execute cycle."""
        self._log("Starting full earning cycle...")

        # 1. Discover
        opps = self.discover(sources=sources)
        self._log(f"Discovered {len(opps)} opportunities")

        # 2. Evaluate
        evaluated = self.evaluate(opps)
        self._log(f"Evaluated {len(evaluated)} opportunities")

        # 3. Filter
        filtered = self.filter(evaluated, max_risk=max_risk)
        self._log(f"Filtered to {len(filtered)} opportunities")

        # 4. Execute (only safe actions)
        results = []
        for opp in filtered:
            if opp.source in ["content", "referral", "faucet"]:
                result = self.execute(opp)
                results.append(result)
                self._log(f"Executed {opp.source}: {result.message}")

        total_revenue = sum(r.opportunity.min_amount
                           for r in results if r.success)

        return PipelineResult(
            success=len(results) > 0,
            message=f"Cycle complete: {len(results)}/{len(filtered)} succeeded",
        )

    # ── Memory Query API ────────────────────────────────────────

    def load_memory(self, limit: int = 1000) -> List[dict]:
        """Load all stored opportunities into cache."""
        opportunities = []
        for opp_type in ["gig", "airdrop", "microtask", "content", "defi", "referral"]:
            opps = self.memory.get_opportunities_by_type(opp_type, limit)
            opportunities.extend(opps)
        return opportunities

    def get_opportunities_by_type(self, opp_type: str, limit: int = 50) -> List[dict]:
        """Get all opportunities of a specific type."""
        return self.memory.get_opportunities_by_type(opp_type, limit)

    def get_recent_outcomes(self, limit: int = 20) -> List[dict]:
        """Get most recent outcomes."""
        return self.memory.get_recent_outcomes(limit)

    def get_platform_reputations(self) -> List[dict]:
        """Get reputation for all platforms."""
        return self.memory.get_all_reputations()

    def get_recent_learned(self, learning_type: str = None, limit: int = 10) -> List[dict]:
        """Get recent learned insights."""
        return self.memory.get_recent_learned(learning_type, limit)

    def search_opportunities_by_keyword(self, keyword: str, limit: int = 20) -> List[dict]:
        """Search opportunities by keyword in title/description."""
        results = []
        for opp_type in ["gig", "airdrop", "microtask", "content", "defi"]:
            opps = self.memory.get_opportunities_by_type(opp_type, limit * 2)
            for opp in opps:
                if keyword.lower() in str(opp).lower():
                    results.append(opp)
                    if len(results) >= limit:
                        return results
        return results

    def get_opportunity_stats(self) -> dict:
        """Get statistics about discovered opportunities."""
        summary = self.memory.get_memory_summary()
        return {
            "total_opportunities": summary["memory_stats"]["cached_opportunities"],
            "platform_stats": summary["top_platforms"],
            "skill_stats": summary["successful_skills"],
            "learning_insights": summary["recent_learned"],
        }