"""Agent 3 - SENTIMENT: public hype signals, no private data.

Sources (all free/public):
- Solana public RPC: token supply + largest accounts -> holder concentration
- DexScreener (already cached): 24h txns + volume -> activity hype

Paper-trading research only. No wallets, no private keys, no live orders.
"""
import json
import time
from pathlib import Path

import requests

RPCS = [
    "https://api.mainnet-beta.solana.com",
]
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens"
CACHE = Path(__file__).parent / "cache"
TIMEOUT = 25
RETRIES = 2


def rugcheck_report(mint):
    """Free RugCheck report: topHolders (pct), authorities, risks, score."""
    r = requests.get(f"{RUGCHECK}/{mint}/report", timeout=TIMEOUT,
                     headers={"User-Agent": "ai-trading-bot-team/research"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def rpc(method, params):
    last = None
    for rpc_url in RPCS:
        for attempt in range(RETRIES):
            try:
                r = requests.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                  timeout=TIMEOUT, headers={"Content-Type": "application/json"})
                if r.status_code == 429:
                    raise RuntimeError("429 rate-limited")
                r.raise_for_status()
                body = r.json()
                if body.get("error"):
                    raise RuntimeError(body["error"])
                return body["result"]["value"]
            except Exception as e:
                last = e
                time.sleep(2 ** attempt * 3)  # 3s, 6s, 12s backoff
    raise RuntimeError(f"all RPCs failed: {last}")


def holder_stats(mint):
    """Holder concentration: RugCheck first (free, no key), RPC fallback."""
    try:
        rep = rugcheck_report(mint)
    except Exception:
        rep = None
    if rep:
        tops = sorted([h.get("pct", 0) for h in rep.get("topHolders", [])], reverse=True)
        return {"source": "rugcheck",
                "supply": (rep.get("token") or {}).get("supply"),
                "holders_sampled": len(tops),
                "top1_pct": round(tops[0], 2) if tops else 0,
                "top5_pct": round(sum(tops[:5]), 2) if tops else 0,
                "mint_authority": rep.get("mintAuthority"),
                "freeze_authority": rep.get("freezeAuthority"),
                "rug_score": rep.get("score_normalised"),
                "risks": [r.get("name") for r in (rep.get("risks") or []) if isinstance(r, dict)]}
    # fallback: raw RPC
    supply = rpc("getTokenSupply", [mint])
    total = float(supply["amount"]) / (10 ** supply["decimals"])
    largest = rpc("getTokenLargestAccounts", [mint])
    accts = largest if isinstance(largest, list) else largest.get("accounts", [])
    amts = sorted([float(a["amount"]) / (10 ** supply["decimals"]) for a in accts], reverse=True)
    top1 = amts[0] / total if amts and total else 0
    top5 = sum(amts[:5]) / total if amts and total else 0
    return {"supply": total, "holders_sampled": len(amts),
            "top1_pct": round(top1 * 100, 2), "top5_pct": round(top5 * 100, 2)}


def hype_score(txns_24h, vol_24h, liq):
    """0-100 activity score (log-scaled, capped)."""
    import math
    t = min(50, 25 * math.log10(1 + (txns_24h or 0)))
    v = min(30, 15 * math.log10(1 + (vol_24h or 0) / 1000))
    l = min(20, 10 * math.log10(1 + (liq or 0) / 5000))
    return round(t + v + l, 1)


def analyze(cands):
    out = []
    for c in cands:
        try:
            hs = holder_stats(c["mint"])
            print(f"  {c['symbol']}: top1={hs['top1_pct']}% top5={hs['top5_pct']}%")
        except Exception as e:
            hs = {"error": str(e)}
            print(f"  {c['symbol']}: holder fetch FAILED ({e})")
        hype = hype_score(c.get("txns_24h"), c.get("volume_24h"), c.get("liquidity_usd"))
        out.append({**c, "holders": hs, "hype_0_100": hype})
        time.sleep(1.2)  # public RPC courtesy delay
    return out


def main():
    cands = json.loads((CACHE / "news_latest.json").read_text())
    print(f"[sentiment] analyzing {len(cands)} candidates...")
    out = analyze(cands)
    (CACHE / "sentiment_latest.json").write_text(json.dumps(out, indent=2))
    print(f"[sentiment] done -> cache/sentiment_latest.json")
    for c in out:
        print(f"  {c['symbol']}: hype={c['hype_0_100']}")
    return out


if __name__ == "__main__":
    main()
