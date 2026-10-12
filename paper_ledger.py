"""PAPER LEDGER: simulated positions in SQLite. REPLACES a live order manager.

There is intentionally NO live-trading code in this project: no wallets,
no private keys, no exchange clients, no order execution. AMBIL signals
only create rows in a local SQLite file with virtual entry/exit prices.
Take-profit / stop-loss are evaluated against later price snapshots.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "data" / "paper.db"
CACHE = ROOT / "cache"
DB.parent.mkdir(exist_ok=True)

TAKE_PROFIT_PCT = 25.0
STOP_LOSS_PCT = -15.0
STARTING_BANKROLL_USD = 100.0
PAPER_SIZE_USD = 20.0  # 20% dari bankroll per posisi, maks 5 posisi terbuka


def conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS positions(
      id INTEGER PRIMARY KEY, opened_at TEXT, mint TEXT, symbol TEXT,
      entry_usd REAL, size_usd REAL, tp_pct REAL, sl_pct REAL,
      status TEXT DEFAULT 'open', closed_at TEXT, exit_usd REAL, pnl_usd REAL,
      exit_reason TEXT)""")
    return c


def open_position(mint, symbol, entry_usd, size=PAPER_SIZE_USD,
                  tp=TAKE_PROFIT_PCT, sl=STOP_LOSS_PCT):
    c = conn()
    c.execute("INSERT INTO positions(opened_at,mint,symbol,entry_usd,size_usd,tp_pct,sl_pct)"
              " VALUES(?,?,?,?,?,?,?)",
              (datetime.now(timezone.utc).isoformat(), mint, symbol, entry_usd, size, tp, sl))
    c.commit()
    pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.close()
    return pid


def mark_to_market(price_map):
    """Evaluate open positions vs {mint: price_now}. Returns closed list."""
    c = conn()
    closed = []
    for pid, mint, sym, entry, size, tp, sl in c.execute(
            "SELECT id,mint,symbol,entry_usd,size_usd,tp_pct,sl_pct FROM positions WHERE status='open'"):
        if mint not in price_map or not price_map[mint]:
            continue
        px = float(price_map[mint])
        pct = (px - entry) / entry * 100
        reason = None
        if pct >= tp:
            reason = f"take-profit +{pct:.1f}%"
        elif pct <= sl:
            reason = f"stop-loss {pct:.1f}%"
        if reason:
            pnl = size * pct / 100
            c.execute("UPDATE positions SET status='closed',closed_at=?,exit_usd=?,pnl_usd=?,exit_reason=?"
                      " WHERE id=?", (datetime.now(timezone.utc).isoformat(), px, round(pnl, 2), reason, pid))
            closed.append((sym, reason, round(pnl, 2)))
    c.commit()
    c.close()
    return closed


def summary():
    c = conn()
    rows = c.execute("SELECT status,COUNT(*),COALESCE(SUM(pnl_usd),0) FROM positions GROUP BY status").fetchall()
    c.close()
    return rows


def record_signals(decisions, max_open=5):
    """Open paper positions for fresh AMBIL signals (skip mints already open).
    Bankroll $100 virtual, $20/posisi, maks 5 posisi terbuka."""
    c = conn()
    already = {r[0] for r in c.execute("SELECT mint FROM positions WHERE status='open'")}
    n_open = c.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
    c.close()
    n = 0
    for d in decisions:
        if d.get("signal") != "AMBIL" or d["mint"] in already:
            continue
        if n_open + n >= max_open:
            break
        try:
            entry = float(d.get("price_usd") or (d.get("chart") or {}).get("price_usd_now"))
        except (TypeError, ValueError):
            continue
        if entry > 0:
            open_position(d["mint"], d.get("symbol"), entry)
            n += 1
    return n


def main():
    decs = json.loads((CACHE / "decision_latest.json").read_text())
    n = record_signals(decs)
    print(f"[ledger] {n} paper positions opened ({len(decs)} signals)")
    for status, cnt, pnl in summary():
        print(f"  {status}: {cnt} pos, P&L ${pnl:,.2f} (SIMULASI)")


if __name__ == "__main__":
    main()
