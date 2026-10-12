"""Agent 5 - RISK: transparent 1-10 risk score. Higher = riskier.

Rules (each adds points, capped 1-10):
  liquidity_usd < 20k: +2 | < 50k: +1
  CoinGecko unlisted: +1 (listed & scored: -1, min floor applies)
  top1 holder > 30%: +2 | > 15%: +1 | holder data unknown: +1
  top5 holders > 60%: +2 | > 40%: +1
  whale concentration top10 tinggi: +2 | sedang: +1 | tak tersedia: +1
  whale distribusi (keluar): +1
  chart volatility > 500: +2 | > 200: +1 | chart unknown: +1
  pair age < 1 day: +1 (brand-new = rug window)

Whale data is supporting evidence, not a verdict: a big wallet can be the
team, a CEX, a market maker, or a bandar — identity is unknown.

Paper-trading research only. No wallets, no private keys, no live orders.
"""
import json
from pathlib import Path

CACHE = Path(__file__).parent / "cache"

BASE_RISK = 2


def score(c):
    pts, reasons = BASE_RISK, ["base 2"]
    liq = c.get("liquidity_usd") or 0
    if liq < 20_000:
        pts += 2; reasons.append("liq<20k +2")
    elif liq < 50_000:
        pts += 1; reasons.append("liq<50k +1")

    cg = (c.get("coingecko") or {}).get("listed")
    if cg is False:
        pts += 1; reasons.append("unlisted-CG +1")
    elif cg is True:
        pts -= 1; reasons.append("listed-CG -1")

    h = c.get("holders") or {}
    if "error" in h or h.get("top1_pct") is None:
        pts += 1; reasons.append("holders-unknown +1")
    else:
        if h["top1_pct"] > 30:
            pts += 2; reasons.append(f"top1 {h['top1_pct']}% +2")
        elif h["top1_pct"] > 15:
            pts += 1; reasons.append(f"top1 {h['top1_pct']}% +1")
        if h.get("top5_pct", 0) > 60:
            pts += 2; reasons.append(f"top5 {h['top5_pct']}% +2")
        elif h.get("top5_pct", 0) > 40:
            pts += 1; reasons.append(f"top5 {h['top5_pct']}% +1")

    ch = c.get("chart") or {}
    if ch.get("volatility") is None:
        pts += 1; reasons.append("chart-unknown +1")
    else:
        if ch["volatility"] > 500:
            pts += 2; reasons.append(f"vol {ch['volatility']} +2")
        elif ch["volatility"] > 200:
            pts += 1; reasons.append(f"vol {ch['volatility']} +1")

    if (c.get("pair_age_days") or 99) < 1:
        pts += 1; reasons.append("age<1d +1")

    w = c.get("whale") or {}
    if "whale" not in c:
        pts += 1; reasons.append("whale belum jalan +1")
    conc = w.get("concentration")
    if conc == "tinggi":
        pts += 2; reasons.append(f"whale top10 {w.get('top10_pct')}% tinggi +2")
    elif conc == "sedang":
        pts += 1; reasons.append(f"whale top10 {w.get('top10_pct')}% sedang +1")
    elif conc == "tak tersedia" or w.get("unavailable"):
        pts += 1; reasons.append("whale-data tak tersedia +1")
    if w.get("status") == "distribusi":
        pts += 1; reasons.append("whale distribusi +1")
    elif w.get("status") == "akumulasi":
        pts -= 1; reasons.append("whale akumulasi -1")

    return max(1, min(10, pts)), reasons


def main():
    src = CACHE / "whale_latest.json"
    if not src.exists():
        src = CACHE / "charts_latest.json"
        print("[risk] whale_latest.json tak ada, pakai charts_latest.json (whale +1)")
    cands = json.loads(src.read_text())
    out = []
    print(f"[risk] scoring {len(cands)} candidates...")
    for c in cands:
        r, reasons = score(c)
        print(f"  {c['symbol']}: risk {r}/10 ({'; '.join(reasons)})")
        out.append({**c, "risk_1_10": r, "risk_reasons": reasons})
    (CACHE / "risk_latest.json").write_text(json.dumps(out, indent=2))
    print("[risk] done -> cache/risk_latest.json")
    return out


if __name__ == "__main__":
    main()
