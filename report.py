"""REPORT: daily paper-trading summary (signals + simulated P&L, IDR)."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import paper_ledger as ledger


def main():
    decs = json.loads((ROOT / "cache" / "decision_latest.json").read_text())
    print("=" * 64)
    print(f"AI TRADING BOT TEAM - PAPER REPORT {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print("SIMULASI EDUKASI - bukan nasihat finansial. Tanpa uang asli.")
    print("=" * 64)
    print(f"\nSINYAL ({len(decs)} kandidat):")
    for d in decs:
        w = d.get("whale") or {}
        print(f"  [{d['signal']}] {d.get('symbol')} score={d.get('total_score')} "
              f"risk={d.get('risk_1_10')}/10 pattern={(d.get('chart') or {}).get('pattern')} "
              f"liq=Rp{(d.get('liquidity_usd') or 0) * ledger.USD_IDR:,.0f} "
              f"whale={w.get('status', 'netral')}")
        print(f"      -> {'; '.join(d.get('decision_reasons', [])[:2])}")
    db = ROOT / "data" / "paper.db"
    print("\nPAPER LEDGER (bankroll virtual Rp200.000, Rp40.000/posisi, TP +25% / SL -15%):");
    if db.exists():
        c = sqlite3.connect(db)
        for r in c.execute("SELECT symbol,entry_usd,status,exit_usd,pnl_usd,exit_reason "
                           "FROM positions ORDER BY id DESC LIMIT 20"):
            print(f"  {r[0]} entry=Rp{r[1]:,.0f} [{r[2]}] exit="
                  f"{('Rp{:,.0f}'.format(r[3]) if r[3] else '-')} pnl=Rp{(r[4] or 0):,.0f} {r[5] or ''}")
        tot = c.execute("SELECT COUNT(*),COALESCE(SUM(pnl_usd),0) FROM positions WHERE status='closed'").fetchone()
        op = c.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
        print(f"  OPEN: {op} | CLOSED: {tot[0]} | total P&L simulasi: Rp{tot[1]:,.0f}")
        c.close()
    else:
        print("  (belum ada posisi)")


if __name__ == "__main__":
    main()
