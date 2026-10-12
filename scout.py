"""Agent 1 - SCOUT: find newly-listed Solana meme coins via DexScreener free API.

Paper-trading research only. No wallets, no private keys, no live orders.
Reads public data, applies liquidity + pair-age filters, caches to JSON.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://api.dexscreener.com"
CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)

# ---- filter defaults (tunable via CLI args in future) ----
MIN_LIQUIDITY_USD = 10_000
MAX_PAIR_AGE_DAYS = 14
MAX_RESULTS = 20
TIMEOUT = 20


def get(url):
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "ai-trading-bot-team/research"})
    r.raise_for_status()
    return r.json()


def latest_profiles():
    """Latest token profiles submitted to DexScreener (proxy for new listings)."""
    return get(f"{BASE}/token-profiles/latest/v1")


def token_pairs(mint):
    """All DexScreener pairs for a token mint."""
    return get(f"{BASE}/latest/dex/tokens/{mint}").get("pairs") or []


def now_ms():
    return int(time.time() * 1000)


def scout(min_liquidity=MIN_LIQUIDITY_USD, max_age_days=MAX_PAIR_AGE_DAYS, limit=MAX_RESULTS):
    profiles = latest_profiles()
    seen = set()
    cands = []
    for p in profiles:
        if p.get("chainId") != "solana":
            continue
        mint = p.get("tokenAddress")
        if not mint or mint in seen:
            continue
        seen.add(mint)
        try:
            pairs = [x for x in token_pairs(mint) if x.get("chainId") == "solana"]
        except Exception as e:
            print(f"  ! pairs fetch failed for {mint[:8]}: {e}")
            continue
        if not pairs:
            continue
        # pick highest-liquidity pair
        pairs.sort(key=lambda x: (x.get("liquidity") or {}).get("usd") or 0, reverse=True)
        b = pairs[0]
        liq = (b.get("liquidity") or {}).get("usd") or 0
        created = b.get("pairCreatedAt")
        age_d = max(0.0, (now_ms() - created) / 86_400_000) if created else None
        if liq < min_liquidity:
            continue
        if age_d is not None and age_d > max_age_days:
            continue
        tx = b.get("txns", {})
        cands.append({
            "mint": mint,
            "name": p.get("name") or (b.get("baseToken") or {}).get("name"),
            "symbol": p.get("symbol") or (b.get("baseToken") or {}).get("symbol"),
            "dex": b.get("dexId"),
            "pair": b.get("pairAddress"),
            "price_usd": b.get("priceUsd"),
            "liquidity_usd": liq,
            "pair_age_days": round(age_d, 2) if age_d is not None else None,
            "volume_24h": (b.get("volume") or {}).get("h24"),
            "txns_24h": sum((v.get("buys", 0) + v.get("sells", 0)) for v in tx.values()) if tx else 0,
            "url": b.get("url"),
        })
        if len(cands) >= limit:
            break
    return cands


def main():
    print(f"[scout] min_liq=${MIN_LIQUIDITY_USD:,} max_age={MAX_PAIR_AGE_DAYS}d")
    cands = scout()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (CACHE / f"scout_{ts}.json").write_text(json.dumps(cands, indent=2))
    (CACHE / "scout_latest.json").write_text(json.dumps(cands, indent=2))
    print(f"[scout] {len(cands)} candidates -> cache/scout_latest.json")
    for c in cands[:10]:
        print(f"  {c['symbol']} liq=${c['liquidity_usd']:,.0f} age={c['pair_age_days']}d price={c['price_usd']}")
    return cands


if __name__ == "__main__":
    main()
