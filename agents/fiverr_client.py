"""
agents/fiverr_client.py — Fiverr gig discovery for MrBot1000.

Fiverr has no public API, so this client uses RSS feeds and
public category page scraping to discover gigs.
"""

import time
import re
from typing import List, Optional
from dataclasses import dataclass, field

import feedparser
import requests
from bs4 import BeautifulSoup

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

    def find_gigs(self, query: str = "python",
                      limit: int = 20) -> List[FiverrGig]:
        """Find gigs via RSS feeds."""
        gigs = []
        for feed_url in self.RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
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