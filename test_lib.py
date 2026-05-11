"""Local test for x402sniper: full /score path on real mints, no paywall."""
import asyncio
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()

import aiohttp
from solders.pubkey import Pubkey

from lib.bundle_check import (
    PUMPFUN_TOTAL_SUPPLY_NORMAL, PUMPFUN_TOTAL_SUPPLY_MAYHEM,
    check_bundle,
)
from lib.launchpad_check import detect_launchpad, fetch_asset, is_self_seeded

HELIUS_API_KEY = os.environ["HELIUS_API_KEY"]
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
PUMP_PROGRAM   = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")


def _bonding_curve(mint: str) -> str:
    pk = Pubkey.from_string(mint)
    bc, _ = Pubkey.find_program_address([b"bonding-curve", bytes(pk)], PUMP_PROGRAM)
    return str(bc)


async def _supply(session, mint):
    async with session.post(HELIUS_RPC_URL, json={
        "jsonrpc":"2.0","id":1,"method":"getTokenSupply",
        "params":[mint, {"commitment":"confirmed"}],
    }, timeout=aiohttp.ClientTimeout(total=8)) as r:
        v = (await r.json()).get("result", {}).get("value") or {}
    ui = float(v.get("uiAmountString") or 0)
    if ui > 1.5e9:
        return PUMPFUN_TOTAL_SUPPLY_MAYHEM, "MAYHEM"
    if ui > 0:
        return PUMPFUN_TOTAL_SUPPLY_NORMAL, "NORMAL"
    return PUMPFUN_TOTAL_SUPPLY_NORMAL, "UNKNOWN"


def _compose_verdict(bundle, launchpad_label, self_seeded):
    reasons = []
    bundle_flags = bundle.get("flags") or []
    no_holders = "NO_HOLDERS" in bundle_flags
    hard_bundle = any(f in {"BUNDLE", "BUNDLE-FRESH"} for f in bundle_flags)
    if no_holders:
        reasons.append("no holders — mint dead/migrated/not yet traded")
    elif hard_bundle:
        reasons.append(f"bundle flags: {','.join(bundle_flags)}")
    elif "SNIPER" in bundle_flags:
        reasons.append("SNIPER concentration")
    else:
        reasons.append("clean bundle check")
    if self_seeded:
        reasons.append("self-seeded pool")
    elif launchpad_label:
        reasons.append(f"launchpad: {launchpad_label}")
    if no_holders:
        return "SKIP", 0, reasons
    if hard_bundle or self_seeded:
        return "SKIP", 15, reasons
    if "SNIPER" in bundle_flags:
        return "WATCH", 45, reasons
    if not launchpad_label:
        return "WATCH", 55, reasons
    return "SNIPE", 80, reasons


async def main():
    if len(sys.argv) > 1:
        mints = sys.argv[1:]
    else:
        mints = []
    if not mints:
        print("usage: test_lib.py <mint> [mint ...]")
        return
    async with aiohttp.ClientSession() as session:
        for i, mint in enumerate(mints, 1):
            sym = mint[:6]
            print(f"─── [{i}/{len(mints)}] {mint}")
            t0 = time.time()
            try:
                Pubkey.from_string(mint)
            except Exception as e:
                print(f"  ✗ invalid pubkey: {e}\n"); continue
            try:
                supply, supply_label = await _supply(session, mint)
                asset = await fetch_asset(session, HELIUS_RPC_URL, mint)
            except Exception as e:
                print(f"  ✗ upstream failed: {e}\n"); continue
            launchpad_label, launchpad_evidence = detect_launchpad(asset, mint)
            self_seeded, self_seeded_evidence = is_self_seeded(launchpad_label, asset)
            bonding = _bonding_curve(mint)
            try:
                bundle = await check_bundle(
                    session, HELIUS_RPC_URL, mint, bonding,
                    total_supply=supply, symbol=sym, verbose=False,
                )
            except Exception as e:
                print(f"  ✗ bundle crashed: {type(e).__name__}: {e}\n"); continue
            verdict, score_val, reasons = _compose_verdict(bundle, launchpad_label, self_seeded)
            elapsed = time.time() - t0
            print(f"  · supply: {supply_label}  has_metadata: {bool(asset)}")
            print(f"  · launchpad: {launchpad_label!r}  ({launchpad_evidence})")
            print(f"  · self_seeded: {self_seeded}  ({self_seeded_evidence})")
            print(f"  · bundle: verdict={bundle.get('verdict')}  flags={bundle.get('flags')}  "
                  f"holders={bundle.get('wallet_count')}  top_cluster={bundle.get('top_cluster_pct')}")
            print(f"  · ▸ VERDICT: {verdict}  score={score_val}")
            print(f"  · reasons: {reasons}")
            print(f"  · time: {elapsed:.2f}s\n")
    from lib import bundle_check as bc
    if bc._wallet_classifier is not None:
        await bc._wallet_classifier.stop()


if __name__ == "__main__":
    asyncio.run(main())
