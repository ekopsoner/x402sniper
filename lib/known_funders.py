"""
Known-funder labelling.

When `_get_wallet_history` resolves a wallet's genesis tx, the resulting funder
address is just an opaque base58 string. This module turns common ones into
human labels so the bundle-check console output reads as:

    funder: 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM  @Binance

instead of forcing the user to paste every funder into Solscan to figure out
whether the trail ends at a CEX hot wallet (= KYC, almost certainly fine) or
goes dark in some unknown wallet (= probably a splitter, suspicious).

Two purposes:
  1. `label_funder(addr)` — return a human label (e.g. "@Binance"), or None.
  2. `KNOWN_FUNDER_ADDRS` — set of all labelled addresses, unioned into
     `BUNDLE_FUNDER_EXCLUDE` at runtime so the bundle grouper does not
     false-flag two unrelated wallets just because they both withdrew from
     the same Binance hot wallet.

Categories:
  - CEX hot wallets   — withdrawal endpoints that fund retail wallets
  - DEX programs      — show up as "funder" when genesis tx was a swap-out
  - Bridges / services — Wormhole, Mayan, etc.

NOTE on staleness: CEX hot wallets do rotate, but slowly (months). The list
below is a starter set; verify new entries against Solscan labels.
"""

from __future__ import annotations


# ── CEX hot wallets ───────────────────────────────────────────────────────────
# Trail ends here = wallet was funded from this exchange's withdrawal endpoint.
# Since CEX withdrawals require KYC, this is essentially "trail ends at known
# entity" rather than "trail goes dark mid-walk".
_CEX_HOT_WALLETS: dict[str, str] = {
    # Binance
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "@Binance",
    "5tzFkiKscXHK5ZXCGbXZxdw7gTdeVKBYW6tBrhuv1NQbY": "@Binance",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S": "@Binance",
    # Coinbase
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "@Coinbase",
    "9obNtb5GGTmqVuZkqsQwf6c2ZVoRz8mMmSjB4fqK8YCK": "@Coinbase",
    "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE": "@Coinbase",
    # Kraken
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5": "@Kraken",
    # OKX
    "5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD": "@OKX",
    "6qPm3FRr6LrHkKp5XK3GpPAZcnjqLcnGNbF7dJaDnTAi": "@OKX",
    # Bybit
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": "@Bybit",
    # KuCoin
    "BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6": "@KuCoin",
    # Gate.io
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w": "@Gate",
    # MEXC
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ": "@MEXC",
    # Crypto.com
    "AobVSwdW9BbpMdJvTqeCN4hPAmh4rHm7vwLnQ5ATSyrS": "@Crypto.com",
    # Robinhood
    "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj": "@Robinhood",
}

# ── DEX / swap programs ───────────────────────────────────────────────────────
# When a wallet's genesis tx is a Jupiter/Raydium/Pump swap, the SOL "funder"
# resolved by the biggest-negative-delta heuristic can be the program account
# itself rather than a real source wallet. Labelling these means the operator
# can see at a glance "this wallet was created by a swap, not a transfer".
_DEX_PROGRAMS: dict[str, str] = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4":  "@Jupiter",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "@Raydium",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":  "@PumpFun",
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA":  "@PumpAMM",
    "PSwapMdSai8tjrEXcxFeQth87xC4rRsa4VA5mhGhXkP":  "@PumpAMM-Buy",
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj":  "@Raydium-Migrate",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "@Orca-CLMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc":  "@Orca-Whirlpool",
}

# ── Bridges / cross-chain services ────────────────────────────────────────────
_BRIDGES: dict[str, str] = {
    "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb":  "@Wormhole",
    "MayanSwap1xNuGVcF6rL6q5pKFaW6XaUcEM6vGnHBgCs": "@Mayan",
}


# Combined registry — single source of truth.
LABELS: dict[str, str] = {
    **_CEX_HOT_WALLETS,
    **_DEX_PROGRAMS,
    **_BRIDGES,
}

# Set of all labelled addresses. Bearcat unions this into BUNDLE_FUNDER_EXCLUDE
# so the funder-grouping logic does not cluster wallets that merely share a CEX
# withdrawal endpoint or a DEX program as their "funder".
KNOWN_FUNDER_ADDRS: frozenset[str] = frozenset(LABELS.keys())


def label_funder(addr: str | None) -> str | None:
    """Return the human label for a funder address, or None if unlabelled."""
    if not addr:
        return None
    return LABELS.get(addr)


def display_funder(addr: str | None, capped: bool = False) -> str:
    """
    Render a funder address for the per-wallet drill-down table.

    States:
      addr=None, capped=True   → "capped"           (wallet too active to walk to genesis)
      addr=None, capped=False  → "complex-origin"   (genesis had no clean SOL delta — bundling-service signature)
      addr set, label known    → "{label}  {addr}"  (e.g. "@Coinbase  H8sMJSCQ…etc")
      addr set, no label       → "{addr}"           (unknown wallet — could be retail, could be a splitter)
    """
    if not addr:
        return "capped" if capped else "complex-origin"
    label = LABELS.get(addr)
    if label:
        return f"{label}  {addr}"
    return addr
