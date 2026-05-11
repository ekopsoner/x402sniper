"""
bundle_check — pump.fun bundle/sniper/funder-cluster detection.

Holder enumeration + funder-cluster analysis for Solana pump.fun mints:
  - Holders enumerated via getTokenLargestAccounts on the mint (top-20 cap is
    fine pre-grad — bonding-curve PDA + dev + early snipers easily fit).
  - Bonding curve PDA is the "pool PDA" excluded from holder ranking.
  - Total supply is fixed: 1B for normal pump.fun, 2B for Mayhem.

Verdict ladder:
  SNIPER       — any single non-curve holder ≥ BUNDLE_LARGE_HOLDER_PCT
  BUNDLE       — top funder cluster (FRESH wallets sharing one funder)
                 ≥ BUNDLE_GROUP_MIN_PCT combined
  BUNDLE-FRESH — fewer than BUNDLE_MIN_ESTABLISHED holders are ESTABLISHED
                 (top is all-fresh, regardless of funder dispersion)
  CLEAN        — otherwise

RPC cost per CLEAN-ish candidate (after wallet_classifier cache warms):
  - 1 getTokenLargestAccounts
  - 1 getMultipleAccounts (resolve owners)
  - For each non-cached holder: walk getSignaturesForAddress + getTransaction
    (~6 RPCs worst case). Cache hit: 0 RPCs.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Optional

import aiohttp

from .known_funders import KNOWN_FUNDER_ADDRS, display_funder
from .wallet_classifier import WalletClassifier

log = logging.getLogger("rugproofx402.bundle")

# ── Knobs ────────────────────────────────────────────────────────────────────
BUNDLE_ENABLED          = os.getenv("BUNDLE_ENABLED",          "true").lower() != "false"
BUNDLE_LARGE_HOLDER_PCT = float(os.getenv("BUNDLE_LARGE_HOLDER_PCT", "4.0"))
BUNDLE_GROUP_MIN_PCT    = float(os.getenv("BUNDLE_GROUP_MIN_PCT",    "5.0"))
BUNDLE_MIN_ESTABLISHED  = int(os.getenv("BUNDLE_MIN_ESTABLISHED",     "1"))
BUNDLE_TOP_HOLDERS_LIMIT= int(os.getenv("BUNDLE_TOP_HOLDERS_LIMIT",  "20"))
BUNDLE_CACHE_FILE       = os.getenv("BUNDLE_CACHE_FILE", "../data/wallet_funders.jsonl")

# Funders that are legitimately shared (CEX hot wallets, DEX programs, etc.)
# — wallets that all came from these don't cluster as a bundle.
_funder_exclude_env = set(filter(None, os.getenv("BUNDLE_FUNDER_EXCLUDE", "").split(",")))
BUNDLE_FUNDER_EXCLUDE = _funder_exclude_env | set(KNOWN_FUNDER_ADDRS)

# Pump.fun token defaults
PUMPFUN_TOTAL_SUPPLY_NORMAL = 1_000_000_000.0   # 1B tokens (UI units)
PUMPFUN_TOTAL_SUPPLY_MAYHEM = 2_000_000_000.0   # 2B tokens (Mayhem AI agent gets +1B)

# ── Singleton classifier ─────────────────────────────────────────────────────
_wallet_classifier: Optional[WalletClassifier] = None
_wallet_classifier_lock = asyncio.Lock()

async def get_wallet_classifier(helius_rpc_url: str) -> WalletClassifier:
    """Module-level singleton — built lazily on first bundle check, kept alive
    for the process lifetime. Cache writes through to wallet_funders.jsonl on
    every classify, so no flush needed at shutdown."""
    global _wallet_classifier
    if _wallet_classifier is not None:
        return _wallet_classifier
    async with _wallet_classifier_lock:
        if _wallet_classifier is None:
            cache_path = Path(__file__).parent / BUNDLE_CACHE_FILE
            wc = WalletClassifier(cache_path, helius_rpc_url)
            await wc.start()
            _wallet_classifier = wc
    return _wallet_classifier

# ── Holder enumeration ──────────────────────────────────────────────────────
async def _get_holders_via_largest_accounts(
    session: aiohttp.ClientSession, helius_rpc_url: str, mint: str,
) -> list[dict]:
    """Top-20 holders via standard RPC getTokenLargestAccounts. Returns
    [{wallet, token_account, amount}, ...] in UI units (decimals applied)."""
    try:
        async with session.post(helius_rpc_url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint, {"commitment": "confirmed"}],
        }, timeout=aiohttp.ClientTimeout(total=10)) as r:
            rows = (await r.json()).get("result", {}).get("value", []) or []
    except Exception as e:
        log.warning(f"getTokenLargestAccounts failed for {mint}: {e}")
        return []
    if not rows:
        return []

    token_accts = [row["address"] for row in rows]
    amounts_ui  = {row["address"]: float(row.get("uiAmount") or 0) for row in rows}

    try:
        async with session.post(helius_rpc_url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getMultipleAccounts",
            "params": [token_accts, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        }, timeout=aiohttp.ClientTimeout(total=10)) as r:
            values = (await r.json()).get("result", {}).get("value", []) or []
    except Exception as e:
        log.warning(f"getMultipleAccounts failed for {mint}: {e}")
        return []

    out: list[dict] = []
    for acct_addr, acc in zip(token_accts, values):
        if not acc:
            continue
        try:
            owner = acc.get("data", {}).get("parsed", {}).get("info", {}).get("owner")
        except Exception:
            owner = None
        if not owner:
            continue
        amt = amounts_ui.get(acct_addr, 0)
        if amt <= 0:
            continue
        out.append({
            "wallet":        owner,
            "token_account": acct_addr,
            "amount":        amt,
        })
    return out

async def get_top_holders(
    session: aiohttp.ClientSession,
    helius_rpc_url: str,
    mint: str,
    bonding_curve_pda: str,
    total_supply: float,
    limit: int = BUNDLE_TOP_HOLDERS_LIMIT,
) -> list[dict]:
    """Aggregate by wallet, exclude bonding curve PDA, sort by pct desc."""
    raw = await _get_holders_via_largest_accounts(session, helius_rpc_url, mint)
    if not raw:
        return []

    by_wallet_total: dict[str, float] = defaultdict(float)
    by_wallet_max:   dict[str, tuple[float, str]] = {}
    for h in raw:
        w = h["wallet"]
        if w == bonding_curve_pda:
            continue
        amt = h["amount"]
        by_wallet_total[w] += amt
        cur = by_wallet_max.get(w)
        if cur is None or amt > cur[0]:
            by_wallet_max[w] = (amt, h["token_account"])

    holders = []
    for w, total_amt in by_wallet_total.items():
        pct = round((total_amt / total_supply) * 100, 3) if total_supply > 0 else 0.0
        holders.append({
            "wallet":        w,
            "token_account": by_wallet_max[w][1],
            "amount":        total_amt,
            "pct":           pct,
        })
    holders.sort(key=lambda x: x["pct"], reverse=True)
    return holders[:limit]

# ── Orchestrator ────────────────────────────────────────────────────────────
async def check_bundle(
    session: aiohttp.ClientSession,
    helius_rpc_url: str,
    mint: str,
    bonding_curve_pda: str,
    total_supply: float = PUMPFUN_TOTAL_SUPPLY_NORMAL,
    symbol: str = "?",
    verbose: bool = True,
) -> dict:
    """Main entry. Returns dict with verdict + per-wallet drill-down.
    verdict ∈ {CLEAN, SKIP, UNKNOWN}; flags lists which checks failed."""
    empty_result = {
        "wallets": [], "wallet_count": 0, "total_pct": 0.0,
        "n_established": 0, "n_fresh": 0, "top_cluster_pct": 0.0,
        "top5_snapshot": [],
    }
    if total_supply <= 0:
        return {**empty_result, "flags": ["UNKNOWN"], "verdict": "UNKNOWN"}

    holders = await get_top_holders(
        session, helius_rpc_url, mint, bonding_curve_pda, total_supply,
        BUNDLE_TOP_HOLDERS_LIMIT,
    )
    if not holders:
        if verbose:
            log.info(f"[{symbol}] No top holders returned — UNKNOWN (mint may be dead, migrated, or not yet traded)")
        return {**empty_result, "flags": ["NO_HOLDERS"], "verdict": "UNKNOWN"}

    classifier = await get_wallet_classifier(helius_rpc_url)
    for h in holders:
        classifier.queue(h["wallet"])
    # Wait briefly for classifier worker pool to drain (cap at 8s).
    for _ in range(40):
        missing = [h["wallet"] for h in holders if classifier.get(h["wallet"]) is None]
        if not missing:
            break
        await asyncio.sleep(0.2)

    funder_groups: dict[str, list] = defaultdict(list)
    for h in holders:
        rec = classifier.get(h["wallet"]) or {}
        h["category"] = rec.get("category", "UNKNOWN")
        h["funder"]   = rec.get("funder")
        h["tx_count"] = rec.get("tx_count", 0)
        if (h["category"] == "FRESH"
                and h["funder"]
                and h["funder"] not in BUNDLE_FUNDER_EXCLUDE):
            funder_groups[h["funder"]].append(h)

    n_fresh       = sum(1 for h in holders if h.get("category") == "FRESH")
    n_established = sum(1 for h in holders if h.get("category") == "ESTABLISHED")

    top_cluster = max(funder_groups.values(),
                      key=lambda g: sum(x["pct"] for x in g),
                      default=[])
    top_cluster_pct = sum(x["pct"] for x in top_cluster)
    bundled = (top_cluster_pct >= BUNDLE_GROUP_MIN_PCT)
    bundled_wallets = {x["wallet"] for x in top_cluster} if bundled else set()

    total_pct = round(sum(h["pct"] for h in holders), 3)
    sniper_holder = next(
        (h for h in holders if h["pct"] >= BUNDLE_LARGE_HOLDER_PCT),
        None,
    )

    flags: list[str] = []
    if sniper_holder:
        flags.append("SNIPER")
    if bundled:
        flags.append("BUNDLE")
    if n_established < BUNDLE_MIN_ESTABLISHED:
        flags.append("BUNDLE-FRESH")
    verdict = "SKIP" if flags else "CLEAN"

    if verbose:
        log.info(f"[{symbol}] ╭─ BUNDLE CHECK  mint:{mint}")
        log.info(
            f"[{symbol}] │  Holders: {len(holders)} (top-{BUNDLE_TOP_HOLDERS_LIMIT} cap, curve excl)  "
            f"holding {total_pct:.2f}%  established:{n_established}  fresh:{n_fresh}"
        )
        log.info(
            f"[{symbol}] │  Top funder cluster: {top_cluster_pct:.2f}% "
            f"({len(top_cluster)} wallets share funder)"
        )
        log.info(f"[{symbol}] ╰─ Per-wallet drill-down:")
        log.info(
            f"[{symbol}]   rk    pct%    txs     cat          "
            f"wallet                                        funder"
        )
        for i, h in enumerate(holders, 1):
            tag = ""
            if h["wallet"] in bundled_wallets:
                tag = "  ← BUNDLE"
            elif h["pct"] >= BUNDLE_LARGE_HOLDER_PCT:
                tag = "  ← SNIPER"
            funder_disp = display_funder(h.get("funder"))
            log.info(
                f"[{symbol}]   {i:>2}.  "
                f"{h['pct']:>6.2f}  "
                f"{h.get('tx_count', 0):>6}  "
                f"{h.get('category', 'UNKNOWN'):<11}  "
                f"{h['wallet']:<44}  "
                f"{funder_disp}"
                f"{tag}"
            )

        if bundled:
            f0 = top_cluster[0].get("funder") if top_cluster else None
            log.info(
                f"[{symbol}] ⚠ BUNDLE — funder {display_funder(f0)} "
                f"funded {len(top_cluster)} wallets ({top_cluster_pct:.2f}% combined)"
            )
            for x in top_cluster:
                log.info(f"[{symbol}]      └─ {x['wallet']}  ({x['pct']:.2f}%)")
        if n_established < BUNDLE_MIN_ESTABLISHED:
            log.info(
                f"[{symbol}] ⚠ BUNDLE-FRESH — only {n_established} established holders "
                f"in top-{BUNDLE_TOP_HOLDERS_LIMIT} (need ≥{BUNDLE_MIN_ESTABLISHED})"
            )
        if sniper_holder:
            log.info(
                f"[{symbol}] ⚠ SNIPER — {sniper_holder['wallet']} holds "
                f"{sniper_holder['pct']:.2f}% (≥ {BUNDLE_LARGE_HOLDER_PCT}%)"
            )

    top5_snapshot = [
        {"wallet": h["wallet"], "amount": h["amount"]}
        for h in holders[:5]
    ]

    return {
        "wallets":         holders,
        "wallet_count":    len(holders),
        "total_pct":       total_pct,
        "flags":           flags,
        "verdict":         verdict,
        "n_established":   n_established,
        "n_fresh":         n_fresh,
        "top_cluster_pct": round(top_cluster_pct, 3),
        "top5_snapshot":   top5_snapshot,
    }
