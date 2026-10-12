"""Agent 6 - DECISION: combine all scores -> AMBIL (paper buy) / LEWATI + reasons.

Rule (transparent, tunable):
  total = 0.35*hype + 0.35*chart_score + 0.30*(100 - risk*10)
  chart_score None or holders unknown already penalize via risk.
  AMBIL (paper) if total >= 55 AND risk <= 7 AND chart pattern not in
  {dumping, chop_whipsaw}. Everything else -> LEWATI with written reason.

Paper-trading research only. No wallets, no private keys, no live orders.
"AMBIL" means a simulated paper-buy recorded in paper_ledger.py, never a real order.
"""
import json
from pathlib import Path

CACHE = Path(__file__).parent / "cache"
THRESHOLD = 55
MAX_RISK = 7
BAD_PATTERNS = {"dumping", "chop_whipsaw"}


def decide(c):
    hype = c.get("hype_0_100") or 0
    ch = c.get("chart") or {}
    chart_score = ch.get("chart_score_0_100")
    risk = c.get("risk_1_10", 10)
    if chart_score is None:
        return "LEWATI", 0.0, ["no chart data"]
    total = round(0.35 * hype + 0.35 * chart_score + 0.30 * (100 - risk * 10), 1)
    reasons = [f"total={total} (hype {hype}, chart {chart_score}, risk {risk}/10)",
               f"pattern={ch.get('pattern')}"]
    if risk > MAX_RISK:
        return "LEWATI", total, reasons + [f"risk {risk} > max {MAX_RISK}"]
    if ch.get("pattern") in BAD_PATTERNS:
        return "LEWATI", total, reasons + [f"pattern {ch.get('pattern')} blocked"]
    if total < THRESHOLD:
        return "LEWATI", total, reasons + [f"total {total} < {THRESHOLD}"]
    return "AMBIL", total, reasons + ["lolos semua filter -> PAPER BUY simulasi"]


def main():
    cands = json.loads((CACHE / "risk_latest.json").read_text())
    out = []
    print(f"[decision] deciding {len(cands)} candidates...")
    for c in cands:
        sig, total, reasons = decide(c)
        print(f"  {c['symbol']}: {sig} ({total}) - {reasons[-1]}")
        out.append({**c, "signal": sig, "total_score": total, "decision_reasons": reasons})
    (CACHE / "decision_latest.json").write_text(json.dumps(out, indent=2))
    print("[decision] done -> cache/decision_latest.json")
    return out


if __name__ == "__main__":
    main()
