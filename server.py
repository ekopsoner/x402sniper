"""x402sniper — pay-per-call post-grad Solana sniping decision support.

Given a Solana mint (typically a recently-graduated pump.fun token), returns
a structured snipe-decision scorecard combining bundle/funder-cluster analysis
with launchpad + self-seeded-pool detection. Signal only — does NOT execute
trades. Buyer's agent decides what to do with the scorecard.

Verdict ladder:
  SNIPE — clean bundle check + recognized launchpad
  WATCH — clean bundle but launchpad unrecognized (possible self-seeded pool)
  SKIP  — bundle/sniper flags present OR self-seeded pool detected

Paid via x402 micropayment on Solana mainnet; facilitator is PayAI.
"""
import logging
import os
from contextlib import asynccontextmanager

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from solders.pubkey import Pubkey
from x402 import x402ResourceServer
from x402.extensions.bazaar.resource_service import (
    OutputConfig,
    declare_discovery_extension,
)
from x402.http import FacilitatorConfig, HTTPFacilitatorClient
from x402.http.middleware.fastapi import payment_middleware
from x402.http.types import PaymentOption, RouteConfig
from x402.mechanisms.svm.exact import register_exact_svm_server

from lib.bundle_check import (
    PUMPFUN_TOTAL_SUPPLY_MAYHEM,
    PUMPFUN_TOTAL_SUPPLY_NORMAL,
    check_bundle,
)
from lib.launchpad_check import detect_launchpad, fetch_asset, is_self_seeded

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("x402sniper")

PAY_TO          = os.environ["SNIPER_PAY_TO"]
NETWORK         = os.environ.get("SNIPER_NETWORK", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp")
FACILITATOR_URL = os.environ.get("SNIPER_FACILITATOR", "https://facilitator.payai.network")

HELIUS_API_KEY  = os.environ.get("HELIUS_API_KEY", "").strip()
HELIUS_RPC_URL  = os.environ.get("HELIUS_RPC_URL") or (
    f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else ""
)

PRICE_PER_CALL = os.environ.get("SNIPER_PRICE", "$0.05")

PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")

facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=FACILITATOR_URL))
x402_server = x402ResourceServer(facilitator)
register_exact_svm_server(x402_server, networks=NETWORK)


def _derive_bonding_curve(mint: str) -> str:
    mint_pk = Pubkey.from_string(mint)
    bc, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint_pk)], PUMP_PROGRAM)
    return str(bc)


async def _get_supply(session: aiohttp.ClientSession, mint: str) -> tuple[float, str]:
    async with session.post(HELIUS_RPC_URL, json={
        "jsonrpc": "2.0", "id": 1,
        "method": "getTokenSupply",
        "params": [mint, {"commitment": "confirmed"}],
    }, timeout=aiohttp.ClientTimeout(total=8)) as r:
        resp = await r.json()
    v = resp.get("result", {}).get("value") or {}
    ui = float(v.get("uiAmountString") or 0)
    if ui > 1.5e9:
        return (PUMPFUN_TOTAL_SUPPLY_MAYHEM, "MAYHEM")
    return (PUMPFUN_TOTAL_SUPPLY_NORMAL, "NORMAL")


SCORE_BAZAAR = declare_discovery_extension(
    input={"mint": "9pQpumpExampleMintAddressOnSolana1234567890abcd"},
    input_schema={
        "type": "object",
        "properties": {
            "mint": {
                "type": "string",
                "description": "Solana SPL token mint address (base58). Optimised for recently-graduated pump.fun tokens but accepts any mint.",
                "minLength": 32,
                "maxLength": 44,
            },
        },
        "required": ["mint"],
    },
    output=OutputConfig(
        example={
            "mint": "9pQpumpExampleMintAddressOnSolana1234567890abcd",
            "verdict": "WATCH",
            "score": 55,
            "reasons": ["clean bundle check", "launchpad unrecognized — possible self-seeded pool"],
            "bundle": {
                "verdict": "CLEAN",
                "flags": [],
                "top_cluster_pct": 0.0,
                "n_established": 3,
                "n_fresh": 16,
            },
            "launchpad": {
                "label": None,
                "evidence": "no recognized launchpad in metadata or mint suffix",
                "self_seeded": True,
            },
        }
    ),
)

ROUTES: dict[str, RouteConfig] = {
    "GET /score/:mint": RouteConfig(
        accepts=PaymentOption(
            scheme="exact",
            pay_to=PAY_TO,
            price=PRICE_PER_CALL,
            network=NETWORK,
            max_timeout_seconds=300,
        ),
        description=(
            "Post-grad Solana sniping decision support. Given a mint (typically "
            "a recently-graduated pump.fun token), returns SNIPE / WATCH / SKIP "
            "verdict with bundle/funder-cluster analysis + launchpad detection. "
            "Signal only — does NOT execute trades. Use to filter snipe candidates."
        ),
        mime_type="application/json",
        extensions=SCORE_BAZAAR,
    ),
}


_session: aiohttp.ClientSession | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session
    _session = aiohttp.ClientSession()
    yield
    await _session.close()
    from lib import bundle_check as bc
    if bc._wallet_classifier is not None:
        await bc._wallet_classifier.stop()


app = FastAPI(
    title="x402sniper",
    description=(
        "Pay-per-call post-grad Solana sniping decision support for AI agents. "
        "Combines bundle/funder forensics with launchpad detection. Signal only — "
        "no trade execution, no key custody. Accepts x402 USDC micropayments on "
        "Solana mainnet via PayAI."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def x402_paywall(request, call_next):
    return await payment_middleware(ROUTES, x402_server)(request, call_next)


@app.get("/", include_in_schema=False)
def root():
    return {
        "service": "x402sniper",
        "version": "0.1.0",
        "x402_version": 2,
        "network": NETWORK,
        "facilitator": FACILITATOR_URL,
        "pay_to": PAY_TO,
        "endpoints": {
            "/score/{mint}": {
                "price_usd": float(PRICE_PER_CALL.lstrip("$")),
                "params": ["mint (path)"],
                "scope": "any Solana SPL mint; optimised for post-grad pump.fun tokens",
                "returns": "SNIPE/WATCH/SKIP verdict + bundle + launchpad analysis",
                "executes_trades": False,
            }
        },
    }


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True, "helius_configured": bool(HELIUS_RPC_URL)}


def _compose_verdict(bundle: dict, launchpad_label: str | None, self_seeded: bool) -> tuple[str, int, list[str]]:
    """Combine bundle + launchpad signals into a single sniper verdict.

    Returns (verdict, score_0_to_100, reasons_list).
    """
    reasons: list[str] = []

    bundle_flags = bundle.get("flags") or []
    hard_bundle = any(f in {"BUNDLE", "BUNDLE-FRESH"} for f in bundle_flags)
    if hard_bundle:
        reasons.append(f"bundle flags present: {', '.join(bundle_flags)}")
    elif "SNIPER" in bundle_flags:
        reasons.append("single-wallet concentration (SNIPER flag)")
    else:
        reasons.append("clean bundle check")

    if self_seeded:
        reasons.append("self-seeded pool (no recognized launchpad)")
    elif launchpad_label:
        reasons.append(f"launchpad recognized: {launchpad_label}")

    # Decision matrix
    if hard_bundle or self_seeded:
        return "SKIP", 15, reasons
    if "SNIPER" in bundle_flags:
        return "WATCH", 45, reasons
    if not launchpad_label:
        return "WATCH", 55, reasons
    return "SNIPE", 80, reasons


@app.get("/score/{mint}")
async def score(mint: str):
    if not HELIUS_RPC_URL:
        raise HTTPException(500, "server not configured: HELIUS_API_KEY missing")
    try:
        Pubkey.from_string(mint)
    except Exception:
        raise HTTPException(400, f"invalid mint address: {mint}")

    # Fetch supply + Metaplex metadata + run bundle check in parallel-ish
    try:
        supply, supply_label = await _get_supply(_session, mint)
        asset = await fetch_asset(_session, HELIUS_RPC_URL, mint)
    except Exception as e:
        log.warning(f"upstream fetch failed for {mint}: {e}")
        raise HTTPException(502, "upstream Helius RPC error")

    launchpad_label, launchpad_evidence = detect_launchpad(asset, mint)
    self_seeded, self_seeded_evidence = is_self_seeded(launchpad_label, asset)

    bonding_curve = _derive_bonding_curve(mint)
    bundle = await check_bundle(
        _session, HELIUS_RPC_URL, mint, bonding_curve,
        total_supply=supply if supply > 0 else PUMPFUN_TOTAL_SUPPLY_NORMAL,
        symbol=mint[:6],
        verbose=False,
    )

    verdict, score_val, reasons = _compose_verdict(bundle, launchpad_label, self_seeded)

    return {
        "mint":          mint,
        "verdict":       verdict,
        "score":         score_val,
        "reasons":       reasons,
        "supply_label":  supply_label,
        "bundle": {
            "verdict":         bundle.get("verdict"),
            "flags":           bundle.get("flags"),
            "top_cluster_pct": bundle.get("top_cluster_pct"),
            "n_established":   bundle.get("n_established"),
            "n_fresh":         bundle.get("n_fresh"),
            "wallet_count":    bundle.get("wallet_count"),
        },
        "launchpad": {
            "label":       launchpad_label,
            "evidence":    launchpad_evidence,
            "self_seeded": self_seeded,
            "self_seeded_evidence": self_seeded_evidence,
        },
        "full_bundle":   bundle,
    }
