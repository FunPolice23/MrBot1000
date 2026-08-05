"""
agents/fiverr_client.py — Fiverr gig discovery for MrBot1000.

Fiverr has no public API, so this client uses RSS feeds and
public category page scraping to discover gigs.
"""

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

FIVERR_BASE = "https://www.fiverr.com"


@dataclass
class FiverrGig:
    id: str
    title: str = ""
    description: str = ""
    budget_usd: float = 0.0
    skills: list = field(default_factory=list)
    url: str = ""
    platform: str = "Fiverr"
    status: str = "new"
    found_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "job_id":      self.id,
            "platform":    self.platform,
            "title":       self.title,
            "description": self.description[:300],
            "budget":      self.budget_usd,
            "skills":      self.skills,
            "url":         self.url,
            "status":      self.status,
            "score":       0.0,
            "notes":       "",
            "found_at":    self.found_at,
            "assigned_to": None,
        }


class FiverrClient:
    """Discover Fiverr gigs via RSS feeds and public pages."""

    RSS_FEEDS = [
        "https://www.fiverr.com/rss/gigs/python",
        "https://www.fiverr.com/rss/gigs/web-development",
        "https://www.fiverr.com/rss/gigs/data-entry",
    ]

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
        })

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

    def find_gigs(self, query: str = "python",
                      limit: int = 20) -> List[FiverrGig]:
        """Find gigs via RSS feeds."""
        gigs = []
        for feed_url in self.RSS_FEEDS:
            try:
                feed = self._parse_feed(feed_url)
                for entry in feed.entries[:limit]:
                    title = entry.get("title", "")
                    if query.lower() not in title.lower():
                        continue

                    link = entry.get("link", "")
                    details = self._scrape_gig_page(link)

                    gigs.append(FiverrGig(
                        id=str(hash(link)),
                        title=title[:200],
                        description=details.get(
                            "description",
                            entry.get("summary", ""),
                        )[:500],
                        budget_usd=details.get("budget", 0.0),
                        skills=details.get("skills", []),
                        url=link,
                        found_at=time.time(),
                    ))
            except Exception:
                continue

        return gigs[:limit]

    def _scrape_gig_page(self, url: str) -> dict:
        """Scrape individual gig page for budget and skills."""
        result = {"budget": 0.0, "skills": [], "description": ""}
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code != 200:
                return result

            if BeautifulSoup is not None:
                soup = BeautifulSoup(resp.text, "html.parser")

                # Extract budget
                price_el = soup.select_one(
                    "[data-testid='price'], .price, .gig-price",
                )
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    price_match = re.search(
                        r'[\d,]+(?:\.\d{1,2})?', price_text,
                    )
                    if price_match:
                        result["budget"] = float(
                            price_match.group().replace(",", ""),
                        )

                # Extract skills/tags
                skill_els = soup.select(
                    "[data-testid='skill-tag'], .skill-tag, .tag",
                )
                result["skills"] = [
                    el.get_text(strip=True) for el in skill_els[:10]
                ]

                # Extract description
                desc_el = soup.select_one(
                    "[data-testid='description'], .gig-description",
                )
                if desc_el:
                    result["description"] = (
                        desc_el.get_text(strip=True)[:500]
                    )

        except Exception:
            pass

        return result