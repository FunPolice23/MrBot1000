"""
agents/airdrop_claimer.py — Auto-claim safe airdrops for MrBot1000.
"""

import time
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from agents.airdrop_scanner import AirdropOpportunity


class AirdropClaimer:
    """Claims airdrops automatically when safe to do so."""

    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),
        })
        self.claimed: List[str] = []
        self.failed: List[str] = []

    def is_safe_to_claim(self, airdrop: AirdropOpportunity) -> bool:
        """Determine if an airdrop is safe to auto-claim."""
        if airdrop.risk_level == "high":
            return False
        if airdrop.requires_kyc:
            return False
        if airdrop.id in self.claimed:
            return False
        return True

    def claim(self, airdrop: AirdropOpportunity) -> bool:
        """Attempt to claim an airdrop."""
        if not self.is_safe_to_claim(airdrop):
            return False

        try:
            resp = self.session.get(
                airdrop.claim_url, timeout=15,
            )
            if resp.status_code != 200:
                self.failed.append(airdrop.id)
                return False

            soup = BeautifulSoup(resp.text, "html.parser")

            # Find claim form/button
            claim_btn = soup.find(
                "button",
                string=re.compile(r"claim|connect|join", re.I),
            )
            if not claim_btn:
                claim_btn = soup.select_one(
                    "[class*='claim'], [id*='claim'], "
                    "a[href*='claim']",
                )

            if claim_btn:
                action = (
                    claim_btn.get("formaction")
                    or claim_btn.get("href")
                )
                if action:
                    result = self.session.get(
                        action, timeout=15,
                    )
                    if result.status_code == 200:
                        self.claimed.append(airdrop.id)
                        return True

            self.failed.append(airdrop.id)
            return False

        except Exception:
            self.failed.append(airdrop.id)
            return False

    def claim_batch(self, airdrops: List[AirdropOpportunity]
                    ) -> dict:
        """Claim multiple airdrops, returning results summary."""
        results = {
            "claimed": 0, "failed": 0, "skipped": 0, "details": [],
        }

        for airdrop in airdrops:
            if not self.is_safe_to_claim(airdrop):
                results["skipped"] += 1
                results["details"].append(
                    f"SKIPPED: {airdrop.title[:60]}",
                )
                continue

            success = self.claim(airdrop)
            if success:
                results["claimed"] += 1
                results["details"].append(
                    f"CLAIMED: {airdrop.title[:60]}",
                )
            else:
                results["failed"] += 1
                results["details"].append(
                    f"FAILED: {airdrop.title[:60]}",
                )

            time.sleep(2)

        return results