"""PAPER LEDGER: simulated positions in SQLite. REPLACES a live order manager.

There is intentionally NO live-trading code in this project: no wallets,
no private keys, no exchange clients, no order execution. AMBIL signals
only create rows in a local SQLite file with virtual entry/exit prices.
Take-profit / stop-loss are evaluated against later price snapshots.
Real-cost model (deducted on every open AND close):
  gas Solana $0.05/tx, DEX fee 0.25% of notional, slippage 2%
  (1% if liquidity > $100k). P&L stored NET (kotor minus costs);
  gross P&L and cost breakdown kept in fee columns for reporting.
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

GAS_USD = 0.05
DEX_FEE_PCT = 0.25
SLIPPAGE_PCT = 2.0
SLIPPAGE_HIGH_LIQ_PCT = 1.0
HIGH_LIQ_USD = 100_000


def trade_cost(notional_usd, liquidity_usd=0):
    """Total one-side cost: gas + fee + slippage."""
    slip = SLIPPAGE_HIGH_LIQ_PCT if (liquidity_usd or 0) > HIGH_LIQ_USD else SLIPPAGE_PCT
    return round(GAS_USD + notional_usd * DEX_FEE_PCT / 100
                 + notional_usd * slip / 100, 4)


def conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS positions(
      id INTEGER PRIMARY KEY, opened_at TEXT, mint TEXT, symbol TEXT,
      entry_usd REAL, size_usd REAL, tp_pct REAL, sl_pct REAL,
      status TEXT DEFAULT 'open', closed_at TEXT, exit_usd REAL, pnl_usd REAL,
      exit_reason TEXT, entry_cost_usd REAL DEFAULT 0, exit_cost_usd REAL DEFAULT 0,
      pnl_gross_usd REAL DEFAULT 0, liquidity_usd REAL DEFAULT 0)""")
    # migrate old DBs
    cols = {r[1] for r in c.execute("PRAGMA table_info(positions)")}
    for col, typ in [("entry_cost_usd", "REAL DEFAULT 0"), ("exit_cost_usd", "REAL DEFAULT 0"),
                     ("pnl_gross_usd", "REAL DEFAULT 0"), ("liquidity_usd", "REAL DEFAULT 0")]:
        if col not in cols:
            c.execute(f"ALTER TABLE positions ADD COLUMN {col} {typ}")
    return c


def open_position(mint, symbol, entry_usd, size=PAPER_SIZE_USD,
                  tp=TAKE_PROFIT_PCT, sl=STOP_LOSS_PCT, liquidity_usd=0):
    c = conn()
    ecost = trade_cost(size, liquidity_usd)
    c.execute("INSERT INTO positions(opened_at,mint,symbol,entry_usd,size_usd,tp_pct,sl_pct,"
              "entry_cost_usd,liquidity_usd) VALUES(?,?,?,?,?,?,?,?,?)",
              (datetime.now(timezone.utc).isoformat(), mint, symbol, entry_usd, size, tp, sl,
               ecost, liquidity_usd))
    c.commit()
    pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.close()
    return pid


def mark_to_market(price_map):
    """Evaluate open positions vs {mint: price_now}. Returns closed list.
    P&L stored NET: gross minus entry cost minus exit cost."""
    c = conn()
    closed = []
    for pid, mint, sym, entry, size, tp, sl, ecost, liq in c.execute(
            "SELECT id,mint,symbol,entry_usd,size_usd,tp_pct,sl_pct,"
            "entry_cost_usd,liquidity_usd FROM positions WHERE status='open'"):
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
            gross = size * pct / 100
            xcost = trade_cost(size * (1 + pct / 100), liq or 0)
            net = round(gross - (ecost or 0) - xcost, 2)
            c.execute("UPDATE positions SET status='closed',closed_at=?,exit_usd=?,"
                      "pnl_usd=?,pnl_gross_usd=?,exit_cost_usd=?,exit_reason=? WHERE id=?",
                      (datetime.now(timezone.utc).isoformat(), px, net,
                       round(gross, 2), xcost, reason, pid))
            closed.append((sym, reason, net))
    c.commit()
    c.close()
    return closed


def unrealized(price_map):
    """(unreal_gross, unreal_net, per-mint net dict) for open positions."""
    c = conn()
    rows = c.execute("SELECT mint,entry_usd,size_usd,entry_cost_usd,liquidity_usd"
                     " FROM positions WHERE status='open'").fetchall()
    c.close()
    ug, un, per = 0.0, 0.0, {}
    for mint, entry, size, ecost, liq in rows:
        px = price_map.get(mint)
        if not px or not entry:
            continue
        gross = size * (float(px) - entry) / entry
        # assume exit cost at current value
        xcost = trade_cost(size * float(px) / entry, liq or 0)
        net = gross - (ecost or 0) - xcost
        ug += gross
        un += net
        per[mint] = round(net, 2)
    return round(ug, 2), round(un, 2), per


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
            open_position(d["mint"], d.get("symbol"), entry,
                          liquidity_usd=d.get("liquidity_usd") or 0)
            n += 1
    return n


def totals():
    """(realized_gross, realized_net, total_costs) over closed positions."""
    c = conn()
    g, n_, ec, xc = c.execute(
        "SELECT COALESCE(SUM(pnl_gross_usd),0),COALESCE(SUM(pnl_usd),0),"
        "COALESCE(SUM(entry_cost_usd),0),COALESCE(SUM(exit_cost_usd),0)"
        " FROM positions WHERE status='closed'").fetchone()
    c.close()
    return round(g, 2), round(n_, 2), round((ec or 0) + (xc or 0), 2)


def main():
    decs = json.loads((CACHE / "decision_latest.json").read_text())
    n = record_signals(decs)
    print(f"[ledger] {n} paper positions opened ({len(decs)} signals)")
    for status, cnt, pnl in summary():
        print(f"  {status}: {cnt} pos, P&L ${pnl:,.2f} (SIMULASI)")


if __name__ == "__main__":
    main()
