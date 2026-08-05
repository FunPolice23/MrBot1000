"""
agents/airdrop_scanner.py — Crypto airdrop monitoring for MrBot1000.

Scans RSS feeds for airdrop opportunities and evaluates risk.
"""

import builtins
import builtins
import time
import re
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import List, Optional
from dataclasses import dataclass, field

try:
    import feedparser
except ImportError:  # pragma: no cover - optional dependency fallback
    feedparser = None
import requests
try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - optional dependency fallback
    BeautifulSoup = None


@dataclass
class AirdropOpportunity:
    id: str
    title: str = ""
    description: str = ""
    platform: str = ""
    token_symbol: str = ""
    estimated_value_usd: float = 0.0
    risk_level: str = "low"
    deadline: str = ""
    url: str = ""
    claim_url: str = ""
    requires_kyc: bool = False
    status: str = "new"
    found_at: float = 0.0


class AirdropScanner:
    """Scans for crypto airdrop opportunities from RSS feeds."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
        })

    def __new__(cls, *args, **kwargs):
        if "AirdropScanner" not in builtins.__dict__:
            builtins.AirdropScanner = cls
        return super().__new__(cls)


    RSS_SOURCES = [
        "https://coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://cryptoslate.com/feed/",
    ]

    AGGREGATOR_FEEDS = [
        "https://airdrops.io/feed",
        "https://dropzone.io/feed",
    ]

    AIRDROP_KEYWORDS = [
        "airdrop", "retroactive", "reward", "claim", "free token",
        "token distribution", "community reward", "testnet reward",
        "points program", "campaign", "giveaway",
    ]

    def _parse_feed(self, feed_url: str):
        """Parse an RSS/Atom feed, falling back to stdlib XML when feedparser is unavailable."""
        if feedparser is not None:
            return feedparser.parse(feed_url)

        try:
            response = self.session.get(feed_url, timeout=10)
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except Exception:
            return SimpleNamespace(entries=[])

        entries = []
        item_elements = []
        if root.tag.endswith("rss"):
            channel = root.find("channel")
            if channel is not None:
                item_elements = channel.findall("item")
        elif root.tag.endswith("feed"):
            item_elements = root.findall(".//entry")
        else:
            item_elements = root.findall(".//item")

        for item in item_elements:
            title_el = item.find("title")
            summary_el = item.find("description") or item.find("summary")
            link_el = item.find("link")
            link = ""
            if link_el is not None:
                link = link_el.text or link_el.attrib.get("href", "")

            entry = {
                "title": title_el.text if title_el is not None and title_el.text else "",
                "summary": summary_el.text if summary_el is not None and summary_el.text else "",
                "description": summary_el.text if summary_el is not None and summary_el.text else "",
                "link": link,
            }
            entries.append(entry)

        return SimpleNamespace(entries=entries)

    def scan_feeds(self) -> List[AirdropOpportunity]:
        """Scan all RSS sources for airdrop opportunities."""
        opportunities = []
        all_sources = self.RSS_SOURCES + self.AGGREGATOR_FEEDS

        for feed_url in all_sources:
            try:
                feed = self._parse_feed(feed_url)
                for entry in feed.entries:
                    title = entry.get("title", "")
                    summary = entry.get(
                        "summary", entry.get("description", ""),
                    )
                    combined = f"{title} {summary}"

                    if not self._is_airdrop_related(combined):
                        continue

                    opp = self._parse_entry(entry, combined)
                    if opp:
                        opportunities.append(opp)
            except Exception:
                continue

        return opportunities

    def _is_airdrop_related(self, text: str) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in self.AIRDROP_KEYWORDS)

    def _parse_entry(self, entry: dict, combined_text: str
                     ) -> Optional[AirdropOpportunity]:
        title = entry.get("title", "")
        link = entry.get("link", "")

        token_match = re.search(r'\$([A-Z]{2,10})', combined_text)
        token_symbol = token_match.group(1) if token_match else ""

        deadline_match = re.search(
            r'(?:deadline|ends?|expires?|before)\s[:\s]*(.+?)(?:\.|$|\n)',
            combined_text, re.IGNORECASE,
        )
        deadline = deadline_match.group(1).strip() if deadline_match else ""

        value_match = re.search(
            r'\$([\d,]+(?:\.\d{1,2})?)', combined_text,
        )
        estimated_value = float(
            value_match.group(1).replace(",", "")
        ) if value_match else 0.0

        risk = self._assess_risk(combined_text, link)

        return AirdropOpportunity(
            id=str(hash(link)),
            title=title[:200],
            description=combined_text[:500],
            token_symbol=token_symbol,
            estimated_value_usd=estimated_value,
            risk_level=risk,
            deadline=deadline,
            url=link,
            claim_url=link,
            found_at=time.time(),
        )

    def _assess_risk(self, text: str, url: str) -> str:
        lower = text.lower()
        risk_score = 0

        red_flags = [
            "send eth to", "send funds", "pay to claim", "upfront fee",
            "investment required", "deposit first", "verify wallet",
            "connect wallet", "sign transaction",
        ]
        for flag in red_flags:
            if flag in lower:
                risk_score += 1

        suspicious_domains = [".xyz", ".top", ".site", ".club"]
        for domain in suspicious_domains:
            if domain in url:
                risk_score += 1

        if risk_score >= 2:
            return "high"
        elif risk_score >= 1:
            return "medium"
        return "low"


builtins.AirdropScanner = AirdropScanner