"""
agents/wallet_manager.py — Wallet management for MrBot1000.

Supports Solana and Ethereum wallets with balance checking.
"""

import os
import json
import time
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from pathlib import Path

import requests


@dataclass
class WalletBalance:
    chain: str
    token: str
    balance: float
    usd_value: float = 0.0


@dataclass
class Transaction:
    tx_hash: str
    chain: str
    type: str
    amount: float
    token: str
    usd_value: float
    timestamp: float
    status: str


class WalletManager:
    """Manages crypto wallets for MrBot1000."""

    def __init__(self, root_folder: str = "."):
        self.root = Path(root_folder).resolve()
        self.wallets_file = self.root / "wallets.json"
        self.wallets: Dict[str, dict] = {}
        self._load_wallets()

    def _load_wallets(self):
        if self.wallets_file.exists():
            try:
                data = json.loads(self.wallets_file.read_text())
                self.wallets = data.get("wallets", {})
            except Exception:
                self.wallets = {}

    def _save_wallets(self):
        data = {"wallets": self.wallets}
        self.wallets_file.write_text(json.dumps(data, indent=2))

    def add_solana_wallet(self, name: str, address: str,
                          private_key: str = ""):
        self.wallets[name] = {
            "type": "solana",
            "address": address,
            "private_key": private_key,
            "added_at": time.time(),
        }
        self._save_wallets()

    def add_ethereum_wallet(self, name: str, address: str,
                            private_key: str = ""):
        self.wallets[name] = {
            "type": "ethereum",
            "address": address,
            "private_key": private_key,
            "added_at": time.time(),
        }
        self._save_wallets()

    def get_balance(self, wallet_name: str
                    ) -> Optional[WalletBalance]:
        wallet = self.wallets.get(wallet_name)
        if not wallet:
            return None

        if wallet["type"] == "solana":
            return self._get_solana_balance(wallet["address"])
        elif wallet["type"] == "ethereum":
            return self._get_ethereum_balance(wallet["address"])
        return None

    def _get_solana_balance(self, address: str
                            ) -> Optional[WalletBalance]:
        rpc_url = os.getenv(
            "SOLANA_RPC_URL",
            "https://api.mainnet-beta.solana.com",
        )
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address],
        }
        try:
            resp = requests.post(
                rpc_url, json=payload, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                lamports = data.get("result", {}).get("value", 0)
                sol_amount = lamports / 1_000_000_000
                usd_value = sol_amount * self._get_sol_price()
                return WalletBalance(
                    chain="solana",
                    token="SOL",
                    balance=sol_amount,
                    usd_value=usd_value,
                )
        except Exception:
            pass
        return None

    def _get_ethereum_balance(self, address: str
                              ) -> Optional[WalletBalance]:
        rpc_url = os.getenv(
            "ETHEREUM_RPC_URL",
            "https://eth.llamarpc.com",
        )
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getBalance",
            "params": [address, "latest"],
        }
        try:
            resp = requests.post(
                rpc_url, json=payload, timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                wei = int(data.get("result", "0"), 16)
                eth_amount = wei / 1e18
                usd_value = eth_amount * self._get_eth_price()
                return WalletBalance(
                    chain="ethereum",
                    token="ETH",
                    balance=eth_amount,
                    usd_value=usd_value,
                )
        except Exception:
            pass
        return None

    def _get_sol_price(self) -> float:
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=solana&vs_currencies=usd",
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get(
                    "solana", {}).get("usd", 0.0)
        except Exception:
            pass
        return 0.0

    def _get_eth_price(self) -> float:
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=ethereum&vs_currencies=usd",
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get(
                    "ethereum", {}).get("usd", 0.0)
        except Exception:
            pass
        return 0.0

    def list_wallets(self) -> List[dict]:
        return [
            {"name": name, "type": w["type"],
             "address": w["address"]}
            for name, w in self.wallets.items()
        ]