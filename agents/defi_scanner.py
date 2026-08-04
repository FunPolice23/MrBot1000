"""
agents/defi_scanner.py — DeFi yield farming scanner for MrBot1000.
"""

import time
from typing import List
from dataclasses import dataclass, field

import requests


@dataclass
class DeFiOpportunity:
    id: str
    protocol: str = ""
    type: str = ""
    token_symbol: str = ""
    apy_percent: float = 0.0
    tvl_usd: float = 0.0
    risk_level: str = "low"
    min_deposit_usd: float = 0.0
    chain: str = "ethereum"
    url: str = ""
    status: str = "new"
    found_at: float = 0.0


class DeFiScanner:
    """Scans DeFi protocols for yield opportunities."""

    PROTOCOLS = {
        "lido": {
            "api": "https://api.lido.fi/v1/staking/apr",
            "chain": "ethereum",
        },
        "aave": {
            "api": "https://api.aave.com/data/protocols",
            "chain": "ethereum",
        },
        "raydium": {
            "api": "https://api.raydium.io/v2/sdk/pools",
            "chain": "solana",
        },
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "MrBot1000/1.0",
            "Accept": "application/json",
        })

    def scan_all(self) -> List[DeFiOpportunity]:
        """Scan all configured protocols."""
        opportunities = []

        for name, config in self.PROTOCOLS.items():
            try:
                opps = self._scan_protocol(name, config)
                opportunities.extend(opps)
            except Exception:
                continue
            time.sleep(1)

        return opportunities

    def _scan_protocol(self, name: str, config: dict
                        ) -> List[DeFiOpportunity]:
        opportunities = []

        try:
            resp = self.session.get(
                config["api"], timeout=15,
            )
            if resp.status_code != 200:
                return opportunities

            data = resp.json()

            if name == "lido":
                apr_data = data if isinstance(data, dict) else {}
                for key, value in apr_data.items():
                    if isinstance(value, (int, float)):
                        opportunities.append(DeFiOpportunity(
                            id=f"lido_{key}",
                            protocol="Lido",
                            type="staking",
                            token_symbol=key.upper(),
                            apy_percent=float(value),
                            risk_level="low",
                            chain="ethereum",
                            url=f"https://lido.fi/staking/{key}",
                            found_at=time.time(),
                        ))

        except Exception:
            pass

        return opportunities

    def filter_by_risk(self, opportunities: List[DeFiOpportunity],
                       max_risk: str = "medium"
                       ) -> List[DeFiOpportunity]:
        risk_order = {"low": 0, "medium": 1, "high": 2}
        max_val = risk_order.get(max_risk, 1)
        return [
            opp for opp in opportunities
            if risk_order.get(opp.risk_level, 2) <= max_val
        ]

    def filter_by_min_tvl(self, opportunities: List[DeFiOpportunity],
                          min_tvl_usd: float = 1000
                          ) -> List[DeFiOpportunity]:
        return [
            opp for opp in opportunities
            if opp.tvl_usd >= min_tvl_usd
        ]