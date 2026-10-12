"""Agent 2 - NEWS: enrich token info via CoinGecko free tier (contract lookup).

Paper-trading research only. No wallets, no private keys, no live orders.
New meme coins are usually NOT listed on CoinGecko yet -> 404 is a signal
(unlisted = less verified = higher risk), not an error. Cached to JSON.
Rate-limit friendly: ~1 req / 7s on free tier.
"""
import json
import time
from pathlib import Path

import requests

BASE = "https://api.coingecko.com/api/v3"
CACHE = Path(__file__).parent / "cache"
CACHE.mkdir(exist_ok=True)
DELAY_S = 7
TIMEOUT = 20


def contract_info(mint):
    """CoinGecko coin data by Solana contract address, or None if unlisted."""
    url = f"{BASE}/coins/solana/contract/{mint}"
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "ai-trading-bot-team/research"})
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        raise RuntimeError("CoinGecko rate limit (429) - retry later")
    r.raise_for_status()
    return r.json()


def summarize(cg):
    if cg is None:
        return {"listed": False}
    md = cg.get("market_data") or {}
    return {
        "listed": True,
        "id": cg.get("id"),
        "market_cap_usd": (md.get("market_cap") or {}).get("usd"),
        "volume_24h_usd": (md.get("total_volume") or {}).get("usd"),
        "homepage": ((cg.get("links") or {}).get("homepage") or [None])[0],
        "twitter": ((cg.get("links") or {}).get("twitter_screen_name")),
        "coingecko_score": cg.get("coingecko_score"),
    }


def enrich(cands):
    out = []
    for c in cands:
        try:
            info = summarize(contract_info(c["mint"]))
            print(f"  {c['symbol']}: listed={info['listed']}")
        except Exception as e:
            info = {"listed": None, "error": str(e)}
            print(f"  {c['symbol']}: ERROR {e}")
        out.append({**c, "coingecko": info})
        time.sleep(DELAY_S)
    return out


def main():
    cands = json.loads((CACHE / "scout_latest.json").read_text())
    print(f"[news] enriching {len(cands)} candidates via CoinGecko...")
    out = enrich(cands)
    (CACHE / "news_latest.json").write_text(json.dumps(out, indent=2))
    n_listed = sum(1 for c in out if (c["coingecko"] or {}).get("listed"))
    print(f"[news] {n_listed}/{len(out)} listed -> cache/news_latest.json")
    return out


if __name__ == "__main__":
    main()
