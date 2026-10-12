"""Agent 4 - CHARTS: trend & volatility from DexScreener pair data.

NOTE: DexScreener's OHLC chart endpoint requires auth (403 on free tier),
so this agent uses the public pair endpoint's price-change ladder
(m5/h1/h6/h24) as the trend/volatility source. Pipeline pattern:
data (price-change ladder) -> manipulate (momentum/volatility values) ->
pattern matching (named setups) -> signal (bullish/bearish/chop score).

Paper-trading research only. No wallets, no private keys, no live orders.
"""
import json
import time
from pathlib import Path

import requests

BASE = "https://api.dexscreener.com"
CACHE = Path(__file__).parent / "cache"
TIMEOUT = 20
DELAY_S = 1.5  # DexScreener free-tier courtesy delay


def fetch_pair(chain, pair_addr, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            r = requests.get(f"{BASE}/latest/dex/pairs/{chain}/{pair_addr}",
                             timeout=TIMEOUT, headers={"User-Agent": "ai-trading-bot-team/research"})
            if r.status_code == 429:
                raise RuntimeError("429 rate-limited")
            r.raise_for_status()
            pairs = r.json().get("pairs") or []
            return pairs[0] if pairs else None
        except Exception as e:
            last = e
            time.sleep(3)
    raise RuntimeError(f"pair fetch unavailable: {last}")


def ladder(pair):
    pc = pair.get("priceChange") or {}
    return {k: float(pc.get(k) or 0) for k in ("m5", "h1", "h6", "h24")}


def indicators(lad):
    """Manipulation step: ladder -> momentum + volatility values."""
    mom = lad["m5"] * 0.4 + lad["h1"] * 0.3 + lad["h6"] * 0.2 + lad["h24"] * 0.1
    vals = list(lad.values())
    vol = max(vals) - min(vals)  # dispersion = volatility proxy
    pos = sum(1 for v in vals if v > 0)
    return {"momentum": round(mom, 2), "volatility": round(vol, 2),
            "timeframes_positive": pos, "ladder": lad}


def match_pattern(ind):
    """Pattern-matching step: named setups from indicator values."""
    m, v = ind["momentum"], ind["volatility"]
    if m > 5 and v < 40:
        return "steady_climb", 75
    if m > 5:
        return "parabolic_hot", 60
    if m < -5:
        return "dumping", 15
    if v > 60:
        return "chop_whipsaw", 35
    if ind["timeframes_positive"] >= 3:
        return "broad_uptrend", 65
    return "flat_dead", 40


def analyze(cands):
    out = []
    for c in cands:
        try:
            pair = fetch_pair("solana", c["pair"])
            if not pair:
                raise RuntimeError("pair not found")
            ind = indicators(ladder(pair))
            pat, score = match_pattern(ind)
            ch = {"price_usd_now": pair.get("priceUsd"), **ind,
                  "pattern": pat, "chart_score_0_100": score}
            print(f"  {c['symbol']}: {pat} mom={ind['momentum']} vol={ind['volatility']} score={score}")
        except Exception as e:
            ch = {"error": str(e), "chart_score_0_100": None}
            print(f"  {c['symbol']}: chart tak tersedia ({e})")
        out.append({**c, "chart": ch})
        time.sleep(DELAY_S)
    return out


def main():
    cands = json.loads((CACHE / "sentiment_latest.json").read_text())
    print(f"[charts] analyzing {len(cands)} candidates...")
    out = analyze(cands)
    (CACHE / "charts_latest.json").write_text(json.dumps(out, indent=2))
    print("[charts] done -> cache/charts_latest.json")
    return out


if __name__ == "__main__":
    main()
