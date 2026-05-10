# x402sniper

Pay-per-call post-grad Solana sniping decision support for AI agents and bots.

Given a Solana mint (typically a recently-graduated pump.fun token), returns
a structured **SNIPE / WATCH / SKIP** verdict combining bundle/funder-cluster
analysis with launchpad detection.

**Signal only — does NOT execute trades. No key custody.**

## Endpoint

```
GET /score/{mint}
```

`$0.05/call` via x402 USDC on Solana mainnet (PayAI facilitator, gasless for buyers).

## What it returns

```json
{
  "verdict": "SNIPE" | "WATCH" | "SKIP",
  "score": 0-100,
  "reasons": ["clean bundle check", "launchpad recognized: pump.fun"],
  "bundle": { "verdict": "...", "flags": [...], "top_cluster_pct": ..., ... },
  "launchpad": { "label": "pump.fun" | null, "self_seeded": false, ... },
  "full_bundle": { /* full per-wallet drill-down */ }
}
```

## Verdict logic

- **SNIPE** — clean bundle check AND launchpad recognized
- **WATCH** — clean bundle but launchpad unrecognized (possible self-seeded pool) OR single SNIPER concentration flag
- **SKIP** — bundle/funder-cluster flags present OR self-seeded pool detected

## Companion tools in this toolkit

- **x402rugproof** — pre-graduation bundle/sniper detection
- **x402rent** — empty-ATA rent recovery (post-trade housekeeping)

## Powered by

- [PayAI Network](https://payai.network) — Solana-first x402 facilitator
- [Helius](https://helius.dev) — Solana RPC + DAS metadata
