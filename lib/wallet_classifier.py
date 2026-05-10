"""
wallet_classifier.py — minimal funder lookup for bundle detection.

For each wallet (signer), finds the wallet that first sent it SOL — the
funder. Bundle wallets share a common funder; organic wallets don't.

Cache is persistent (data/wallet_funders.jsonl, append-only) so we never
re-fetch the same wallet across runs.

Public API:
  classifier = WalletClassifier(cache_path, rpc_url)
  await classifier.start()
  classifier.queue(wallet)            # fire-and-forget classification
  funder = classifier.get(wallet)     # returns funder str or None or "OLD"/"UNKNOWN"
"""
import asyncio
import aiohttp
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("classifier")


class WalletClassifier:
    PAGE_LIMIT       = 1000           # one page = 1000 sigs
    MAX_PAGES        = 1              # only first page; >1000 sigs = ESTABLISHED
    FETCH_CONCURRENCY = 4
    FETCH_TIMEOUT_S   = 8

    def __init__(self, cache_path: Path, rpc_url: str):
        self.cache_path = cache_path
        self.rpc_url    = rpc_url
        self.cache: dict[str, dict] = {}
        self._queue: asyncio.Queue   = asyncio.Queue()
        self._session: aiohttp.ClientSession | None = None
        self._workers: list[asyncio.Task] = []
        self._inflight: set[str] = set()

    async def start(self):
        # Load cache from disk (one record per wallet, last-write wins).
        if self.cache_path.exists():
            with open(self.cache_path) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if "wallet" in r:
                            self.cache[r["wallet"]] = r
                    except Exception:
                        pass
            log.info(f"loaded {len(self.cache)} cached wallet records")
        self._session = aiohttp.ClientSession()
        for _ in range(self.FETCH_CONCURRENCY):
            self._workers.append(asyncio.create_task(self._worker()))

    async def stop(self):
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        if self._session:
            await self._session.close()

    def get(self, wallet: str) -> dict | None:
        return self.cache.get(wallet)

    def queue(self, wallet: str):
        if not wallet or wallet in self.cache or wallet in self._inflight:
            return
        self._inflight.add(wallet)
        self._queue.put_nowait(wallet)

    async def _worker(self):
        while True:
            try:
                wallet = await self._queue.get()
                rec = await self._classify(wallet)
                if rec:
                    self.cache[wallet] = rec
                    try:
                        with open(self.cache_path, "a") as f:
                            f.write(json.dumps(rec) + "\n")
                    except Exception as e:
                        log.warning(f"cache write failed: {e}")
                self._inflight.discard(wallet)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.debug(f"worker error: {e}")

    async def _rpc(self, method: str, params: list) -> dict | None:
        try:
            async with self._session.post(self.rpc_url, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            }, timeout=aiohttp.ClientTimeout(total=self.FETCH_TIMEOUT_S)) as r:
                return (await r.json()).get("result")
        except Exception:
            return None

    async def _classify(self, wallet: str) -> dict | None:
        sigs = await self._rpc("getSignaturesForAddress",
                               [wallet, {"limit": self.PAGE_LIMIT}])
        if sigs is None:
            return None
        rec = {
            "wallet":      wallet,
            "classified_at": time.time(),
            "tx_count":    len(sigs),
            "funder":      None,
            "category":    None,
        }
        if not sigs:
            rec["category"] = "UNKNOWN"
            return rec
        if len(sigs) >= self.PAGE_LIMIT:
            # Established wallet — not a bundle target.
            rec["category"] = "ESTABLISHED"
            return rec

        oldest_sig = sigs[-1]["signature"]
        oldest_bt  = sigs[-1].get("blockTime")
        rec["first_blocktime"] = oldest_bt

        tx = await self._rpc("getTransaction", [oldest_sig, {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        }])
        if not tx:
            rec["category"] = "FRESH"
            return rec

        try:
            msg   = tx["transaction"]["message"]
            meta  = tx.get("meta", {})
            accts = msg.get("accountKeys", [])
            pre   = meta.get("preBalances", []) or []
            post  = meta.get("postBalances", []) or []
            wallet_idx = None
            keys: list[str] = []
            for k in accts:
                ak = k if isinstance(k, str) else (k.get("pubkey") or "")
                keys.append(ak)
                if ak == wallet:
                    wallet_idx = len(keys) - 1
            if wallet_idx is None or wallet_idx >= len(pre):
                rec["category"] = "FRESH"
                return rec
            delta = post[wallet_idx] - pre[wallet_idx]
            if delta <= 0:
                rec["category"] = "FRESH"
                return rec
            # Funder = the account with the largest negative delta.
            best_neg = 0
            funder   = None
            for i, k in enumerate(keys):
                if i == wallet_idx:
                    continue
                if i >= len(pre):
                    continue
                neg = pre[i] - post[i]
                if neg > best_neg:
                    best_neg = neg
                    funder   = k
            rec["funder"]   = funder
            rec["category"] = "FRESH"
            return rec
        except Exception:
            rec["category"] = "FRESH"
            return rec
