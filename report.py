"""REPORT: daily paper-trading summary (signals + simulated P&L)."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    decs = json.loads((ROOT / "cache" / "decision_latest.json").read_text())
    print("=" * 64)
    print(f"AI TRADING BOT TEAM - PAPER REPORT {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print("SIMULASI EDUKASI - bukan nasihat finansial. Tanpa uang asli.")
    print("=" * 64)
    print(f"\nSINYAL ({len(decs)} kandidat):")
    for d in decs:
        print(f"  [{d['signal']}] {d.get('symbol')} score={d.get('total_score')} "
              f"risk={d.get('risk_1_10')}/10 pattern={(d.get('chart') or {}).get('pattern')} "
              f"liq=${(d.get('liquidity_usd') or 0):,.0f}")
        print(f"      -> {'; '.join(d.get('decision_reasons', [])[:2])}")
    db = ROOT / "data" / "paper.db"
    print("\nPAPER LEDGER (bankroll virtual $100, $20/posisi, TP +25% / SL -15%):");
    if db.exists():
        c = sqlite3.connect(db)
        for r in c.execute("SELECT symbol,entry_usd,status,exit_usd,pnl_usd,exit_reason "
                           "FROM positions ORDER BY id DESC LIMIT 20"):
            print(f"  {r[0]} entry={r[1]} [{r[2]}] exit={r[3]} pnl={r[4]} {r[5] or ''}")
        tot = c.execute("SELECT COUNT(*),COALESCE(SUM(pnl_usd),0) FROM positions WHERE status='closed'").fetchone()
        op = c.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
        print(f"  OPEN: {op} | CLOSED: {tot[0]} | total P&L simulasi: ${tot[1]:,.2f}")
        c.close()
    else:
        print("  (belum ada posisi)")


if __name__ == "__main__":
    main()
