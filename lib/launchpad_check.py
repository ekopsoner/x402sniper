"""launchpad_check — Solana mint launchpad + self-seeded pool detection.

Uses Helius DAS getAsset (Metaplex metadata) to identify whether a mint was
launched via a recognized launchpad (pump.fun, letsbonk, moonshot, rapidlaunch).

The post-grad rug class that bearcat field-tested 2026-05-09 has a distinctive
signature: token sits on `pump_amm` (the post-graduation AMM) but the underlying
metadata does NOT reference a recognized launchpad. That mismatch indicates a
self-seeded liquidity pool — the dev seeded their own LP straight to the AMM
without going through a fair-launch bonding curve, which is rugable by design.

No Birdeye / GMGN dependency — pure Helius DAS.
"""
from __future__ import annotations

import logging
from typing import Optional

import aiohttp

log = logging.getLogger("x402sniper.launchpad")


KNOWN_LAUNCHPADS = ("rapidlaunch", "pump.fun", "moonshot", "letsbonk", "bonk.fun")
PUMP_AMM_SUFFIX = "pump"
LETSBONK_SUFFIX = "bonk"


async def fetch_asset(
    session: aiohttp.ClientSession, helius_rpc_url: str, mint: str
) -> dict:
    """Return parsed Metaplex metadata via Helius DAS getAsset. {} on failure."""
    try:
        async with session.post(helius_rpc_url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getAsset",
            "params": {"id": mint},
        }, timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json()
        return data.get("result") or {}
    except Exception as e:
        log.warning(f"getAsset failed for {mint}: {e}")
        return {}


def detect_launchpad(asset: dict, mint: str) -> tuple[Optional[str], str]:
    """Returns (launchpad_label, evidence) from Metaplex metadata.

    launchpad_label is one of KNOWN_LAUNCHPADS or None if no launchpad detected.
    evidence is a human-readable string explaining how it was identified.
    """
    content = asset.get("content") or {}
    metadata = content.get("metadata") or {}
    description = (metadata.get("description") or "").lower()
    name = (metadata.get("name") or "").lower()

    # Off-chain JSON often includes the description; check there first.
    for lp in KNOWN_LAUNCHPADS:
        if lp in description or lp in name:
            return lp, f"metadata mentions '{lp}'"

    # Weak fallback: mint suffix conventions (pump.fun mints end in 'pump',
    # letsbonk mints end in 'bonk'). False-positive prone but useful when
    # metadata is stripped.
    ml = mint.lower()
    if ml.endswith(PUMP_AMM_SUFFIX):
        return "pump.fun", "mint address ends in 'pump' (suffix heuristic)"
    if ml.endswith(LETSBONK_SUFFIX):
        return "letsbonk", "mint address ends in 'bonk' (suffix heuristic)"

    return None, "no recognized launchpad in metadata or mint suffix"


def is_self_seeded(launchpad: Optional[str], asset: dict) -> tuple[bool, str]:
    """Self-seeded = AMM listing with no recognized launchpad.

    Indicator that the dev seeded liquidity directly to an AMM rather than
    graduating through a fair-launch curve. Strongly correlated with the
    post-grad consolidation rug class (BXM8 field test 2026-05-09).
    """
    if launchpad is not None:
        return False, "launchpad recognized — likely went through fair launch"

    # If no launchpad detected, look for evidence the asset is tradeable on an AMM
    # (markets exist). Without Birdeye, we treat absence-of-launchpad as the
    # signal; the buyer-side agent can call x402rugproof or read on-chain to
    # confirm AMM presence.
    return True, "no recognized launchpad in metadata — possible self-seeded AMM listing"
