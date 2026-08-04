"""
agents/upwork_client.py — Upwork API client for MrBot1000.

Wraps the Upwork OAuth2 API for finding freelance gigs.
Requires: UPWORK_CLIENT_ID, UPWORK_CLIENT_SECRET,
          UPWORK_ACCESS_TOKEN, UPWORK_REFRESH_TOKEN in .env
"""

import time
from typing import List, Optional
from dataclasses import dataclass, field

import requests

UPWORK_BASE = "https://api.upwork.com/v2"


@dataclass
class UpworkGig:
    id: str
    title: str = ""
    description: str = ""
    budget_usd: float = 0.0
    skills: list = field(default_factory=list)
    url: str = ""
    platform: str = "Upwork"
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


class UpworkClient:
    """Upwork OAuth2 client with token refresh."""

    def __init__(self, client_id: str, client_secret: str,
                 access_token: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_expires_at = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MrBot1000/1.0",
        })

    def _refresh_token(self) -> bool:
        """Refresh OAuth2 access token."""
        now = time.time()
        if self.access_token and now < self.token_expires_at - 60:
            return True

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        try:
            resp = requests.post(
                "https://www.upwork.com/api/v2/oauth2/token",
                data=data, timeout=15,
            )
            if resp.status_code == 200:
                token_data = resp.json()
                self.access_token = token_data.get("access_token", "")
                self.token_expires_at = time.time() + token_data.get(
                    "expires_in", 3600,
                )
                return True
        except Exception:
            pass
        return False

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        """Make authenticated request with auto token refresh."""
        if not self._refresh_token():
            return None

        url = f"{UPWORK_BASE}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        headers["Content-Type"] = "application/json"

        for attempt in range(3):
            try:
                resp = requests.request(
                    method, url, headers=headers, timeout=15, **kwargs,
                )
                if resp.status_code == 401:
                    if self._refresh_token():
                        continue
                    return None
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException:
                if attempt == 2:
                    return None
                time.sleep(2 ** attempt)
        return None

    def find_gigs(self, q: str = "python", category: str = "software-dev",
                  limit: int = 20) -> List[UpworkGig]:
        """Search for freelance gigs."""
        data = self._request(
            "GET",
            f"/hr/v2/search-jobs?q={q}&category={category}&limit={limit}",
        )
        if not data:
            return []

        gigs = []
        jobs = data.get("jobs", data if isinstance(data, list) else [])
        for job in jobs:
            if not isinstance(job, dict):
                continue
            gig_id = str(job.get("job_id", job.get("id", "")))
            if not gig_id:
                continue
            gigs.append(UpworkGig(
                id=gig_id,
                title=str(job.get("title", ""))[:200],
                description=str(
                    job.get("description", job.get("short_description", ""))
                )[:500],
                budget_usd=float(
                    job.get("budget", job.get("fixed_price", 0))
                ),
                skills=job.get("skills", []),
                url=str(
                    job.get("url",
                            f"https://www.upwork.com/jobs/{gig_id}")
                ),
                found_at=time.time(),
            ))
        return gigs
