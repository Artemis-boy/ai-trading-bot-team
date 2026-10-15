"""PAPER LEDGER: simulated positions in SQLite. REPLACES a live order manager.

There is intentionally NO live-trading code in this project: no wallets,
no private keys, no exchange clients, no order execution. AMBIL signals
only create rows in a local SQLite file with virtual entry/exit prices.
Take-profit / stop-loss are evaluated against later price snapshots.
Currency: IDR. Real-cost model Axiom (deducted on every open AND close):
  fee Axiom 0,95%, priority fee 0,001 SOL (kurs 1 SOL = Rp2.000.000),
  fee DEX 0,25%, slippage 2% (1% jika likuiditas > $100k).
P&L stored NET (kotor minus costs); gross P&L and cost breakdown kept
in fee columns for reporting.
"""
# --- Entry filter utilities ---

# ---------------------------------------------------------------------------
# Top‑up handling
# ---------------------------------------------------------------------------

def record_topup(amount: int, date: str = None) -> None:
    """Record a cash top‑up.
    * Increases ``cash_balance`` and ``total_equity`` by *amount*.
    * Appends a dict ``{"date": date, "amount": amount}`` to ``topup_history`` JSON.
    * If *date* is None, uses current ISO date.
    """
    import json, datetime
    # Use WITA (UTC+8) for the date stamp
    wita_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    c = conn()


    entry_date = date or wita_now.date().isoformat()

    # fetch current portfolio row
    row = c.execute('SELECT cash_balance, total_equity, topup_history FROM portfolio WHERE id=1').fetchone()
    if not row:
        raise RuntimeError('Portfolio row missing')
    cash, equity, topup_json = row
    # parse existing history
    try:
        history = json.loads(topup_json)
    except Exception:
        history = []
    # prepare entry
    entry_date = date or datetime.datetime.utcnow().date().isoformat()
    history.append({"date": entry_date, "amount": amount})
    new_json = json.dumps(history)
    # update portfolio
    new_cash = cash + amount
    new_equity = equity + amount
    c.execute('UPDATE portfolio SET cash_balance = ?, total_equity = ?, topup_history = ? WHERE id = 1',
              (new_cash, new_equity, new_json))
    c.commit()
    c.close()


from datetime import datetime, timezone, timedelta

def token_age_ok(decision):
    """Return True if token age >= 1 hour (3600 seconds).
    Uses ``pair_age_days`` if present; if missing, treat as unknown and accept.
    """
    pair_days = decision.get('pair_age_days')
    if pair_days is not None:
        try:
            age_seconds = float(pair_days) * 86400
            return age_seconds >= 3600
        except Exception:
            pass
    # Unknown age → accept
    return True

def liquidity_ok(decision):
    """Return True if liquidity >= Rp500 million (≈ $31k)."""
    # Use explicit liquidity fields; if missing, treat as unknown → accept.
    liq_idr = decision.get('liquidity_idr')
    if liq_idr is None:
        liq_usd = decision.get('liquidity_usd')
        if liq_usd is not None:
            liq_idr = liq_usd * 16000
    # If still None, liquidity unknown → do not block
    if liq_idr is None:
        return True
    return liq_idr >= 500_000_000


def allow_parabolic_hot(decision):
    """Extra confirmations for pattern ``parabolic_hot``.
    - Token age ≥ 1 hour (handled by token_age_ok).
    - Liquidity must be stable: ``liquidity_change_pct`` must NOT be < -30 (no >30 % drop).
    - Price pull‑back from recent peak must be ≥ 5 % (price fell ≥5 %).
    Returns True only if all three conditions are met.
    """
    # Age check – token_age_ok already uses 1‑hour threshold
    if not token_age_ok(decision):
        return False
    # Liquidity stability check
    liq_change = decision.get('liquidity_change_pct') or 0
    if liq_change < -30:
        return False
    # Price pull‑back calculation
    price_old = float(decision.get('price_usd') or 0)
    price_now = float(decision.get('chart', {}).get('price_usd_now') or 0)
    if price_old <= 0:
        return False
    pullback_pct = (price_old - price_now) / price_old * 100
    if pullback_pct < 5:
        return False
    return True

def not_parabolic_hot(decision):
    """Return True if pattern is not 'parabolic_hot'."""
    return decision.get('pattern') != 'parabolic_hot'

def lp_locked(decision):
    """Return True if LP is locked/burned OR if data missing (unknown) → accept."""
    # If both fields missing, treat as unknown and allow
    if decision.get('lp_locked') is None and decision.get('lp_burned') is None:
        return True
    return bool(decision.get('lp_locked') or decision.get('lp_burned'))


def should_enter(decision):
    """Apply entry filters.
    * If pattern == 'parabolic_hot' → invoke ``allow_parabolic_hot`` (age + liquidity stability + pull‑back).
    * Otherwise (e.g., 'steady_climb') → apply the basic filters only (age, liquidity, LP‑lock).
    """
    pattern = (decision.get('chart') or {}).get('pattern')
    if pattern == 'parabolic_hot':
        return allow_parabolic_hot(decision)
    # Non‑parabolic patterns: basic filters only
    return token_age_ok(decision) and liquidity_ok(decision) and lp_locked(decision)

import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "data" / "paper.db"
CACHE = ROOT / "cache"
DB.parent.mkdir(exist_ok=True)

# Initialize portfolio table (single‑row ledger) if missing
def _init_portfolio(conn):
    # Existing portfolio table
    conn.execute('''CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY,
        cash_balance INTEGER NOT NULL,
        total_equity INTEGER NOT NULL,
        realized_pnl INTEGER NOT NULL,
        unrealized_pnl INTEGER NOT NULL,
        total_writeoff INTEGER NOT NULL,
        topup_history TEXT NOT NULL
    )''')
    # Create cash transaction log (append‑only)
    conn.execute('''CREATE TABLE IF NOT EXISTS cash_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        event TEXT NOT NULL,
        position_id INTEGER,
        cash_delta INTEGER NOT NULL,
        balance INTEGER NOT NULL
    )''')
    # ensure a single row exists (id=1)
    cur = conn.execute('SELECT COUNT(*) FROM portfolio')
    if cur.fetchone()[0] == 0:
        conn.execute('INSERT INTO portfolio (id, cash_balance, total_equity, realized_pnl, unrealized_pnl, total_writeoff, topup_history) VALUES (1, ?, ?, 0, 0, 0, "[]")',
                     (STARTING_BANKROLL, STARTING_BANKROLL))
    conn.commit()
    return conn
    conn.execute('''CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY,
        cash_balance INTEGER NOT NULL,
        total_equity INTEGER NOT NULL,
        realized_pnl INTEGER NOT NULL,
        unrealized_pnl INTEGER NOT NULL,
        total_writeoff INTEGER NOT NULL,
        topup_history TEXT NOT NULL
    )''')
    # ensure a single row exists (id=1)
    cur = conn.execute('SELECT COUNT(*) FROM portfolio')
    if cur.fetchone()[0] == 0:
        # start with STARTING_BANKROLL as cash and equity; other fields zero
        conn.execute('INSERT INTO portfolio (id, cash_balance, total_equity, realized_pnl, unrealized_pnl, total_writeoff, topup_history) VALUES (1, ?, ?, 0, 0, 0, "[]")',
                     (STARTING_BANKROLL, STARTING_BANKROLL))
        conn.commit()

TAKE_PROFIT_PCT = 25.0
STOP_LOSS_PCT = -15.0
STARTING_BANKROLL = 350_000  # Rp (after top‑up of Rp150.000 on 13 Oct 2026 18:06)
PAPER_SIZE = 40_000  # Rp per posisi (20%), maks 5 posisi terbuka

USD_IDR = 16_000  # kurs konversi harga token USD -> IDR
SOL_IDR = 2_000_000  # kurs 1 SOL (model biaya Axiom)
PRIORITY_FEE_IDR = 0.001 * SOL_IDR  # Rp2.000 per sisi
AXIOM_FEE_PCT = 0.95
DEX_FEE_PCT = 0.25
SLIPPAGE_PCT = 2.0
SLIPPAGE_HIGH_LIQ_PCT = 1.0
HIGH_LIQ_USD = 100_000


def trade_cost(notional_idr, liquidity_usd=0):
    """Total one-side cost in IDR: priority + axiom fee + dex fee + slippage."""
    slip = SLIPPAGE_HIGH_LIQ_PCT if (liquidity_usd or 0) > HIGH_LIQ_USD else SLIPPAGE_PCT
    return round(PRIORITY_FEE_IDR + notional_idr * (AXIOM_FEE_PCT + DEX_FEE_PCT + slip) / 100, 0)


def conn():
    c = sqlite3.connect(DB)
    # Ensure portfolio table exists (single‑row ledger)
    _init_portfolio(c)
    # Create positions table if missing
    c.execute("""CREATE TABLE IF NOT EXISTS positions(
      id INTEGER PRIMARY KEY, opened_at TEXT, mint TEXT, symbol TEXT,
      entry_usd REAL, size_usd REAL, tp_pct REAL, sl_pct REAL,
      status TEXT DEFAULT 'open', closed_at TEXT, exit_usd REAL, pnl_usd REAL,
      exit_reason TEXT, entry_cost_usd REAL DEFAULT 0, exit_cost_usd REAL DEFAULT 0,
      pnl_gross_usd REAL DEFAULT 0, liquidity_usd REAL DEFAULT 0)""")
    # Get existing column names
    cols = {r[1] for r in c.execute("PRAGMA table_info(positions)")}
    # Add missing is_writeoff flag
    if 'is_writeoff' not in cols:
        c.execute('ALTER TABLE positions ADD COLUMN is_writeoff INTEGER DEFAULT 0')
    # Add any other missing columns
    for col, typ in [("entry_cost_usd", "REAL DEFAULT 0"), ("exit_cost_usd", "REAL DEFAULT 0"),
                     ("pnl_gross_usd", "REAL DEFAULT 0"), ("liquidity_usd", "REAL DEFAULT 0"),
                     ("miss_count", "INTEGER DEFAULT 0")]:
        if col not in cols:
            c.execute(f"ALTER TABLE positions ADD COLUMN {col} {typ}")
    return c


def usd_to_idr(x):
    """Harga token dari API dalam USD -> IDR."""
    return (x or 0) * USD_IDR


def open_position(mint, symbol, entry_price_usd, size=PAPER_SIZE,
                  tp=TAKE_PROFIT_PCT, sl=STOP_LOSS_PCT, liquidity_usd=0):
    entry = usd_to_idr(entry_price_usd)
    c = conn()
    ecost = trade_cost(size, liquidity_usd)
    # Insert position first
    c.execute("INSERT INTO positions(opened_at,mint,symbol,entry_usd,size_usd,tp_pct,sl_pct,"
              "entry_cost_usd,liquidity_usd) VALUES(?,?,?,?,?,?,?,?,?)",
              (datetime.now(timezone.utc).isoformat(), mint, symbol, entry, size, tp, sl,
               ecost, liquidity_usd))
    pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    # Reserve capital in portfolio
    c.execute("UPDATE portfolio SET cash_balance = cash_balance - ?, total_equity = total_equity - ? WHERE id=1",
              (size + ecost, size + ecost))
    # Log reservation (negative delta)
    c.execute("INSERT INTO cash_log (ts, event, position_id, cash_delta, balance) VALUES (?,?,?, ?, (SELECT cash_balance FROM portfolio WHERE id=1))",
              (datetime.now(timezone(timedelta(hours=8))).isoformat(), 'OPEN', pid, -(size + ecost)))
    c.commit()
    c.close()
    return pid




def _update_equity(c):
    """Re‑compute total_equity = cash_balance + nilai posisi terbuka (size+entry_cost)."""
    cash = c.execute('SELECT cash_balance FROM portfolio WHERE id=1').fetchone()[0]
    open_val = c.execute('SELECT COALESCE(SUM(size_usd + entry_cost_usd),0) FROM positions WHERE status="open"').fetchone()[0]
    total = cash + (open_val or 0)
    c.execute('UPDATE portfolio SET total_equity = ? WHERE id=1', (total,))
    # No commit here; caller will commit.

def mark_to_market(price_map_usd):
    """Evaluate open positions vs {mint: price_now_usd}. Returns closed list.
    Prices converted to IDR; P&L stored NET: gross minus entry cost minus exit cost.
    Updates portfolio cash, equity, realized P&L and write‑off counters atomically."""
    price_map = {m: usd_to_idr(p) for m, p in (price_map_usd or {}).items() if p}
    c = conn()
    closed = []
    for pid, mint, sym, entry, size, tp, sl, ecost, liq in c.execute(
            "SELECT id,mint,symbol,entry_usd,size_usd,tp_pct,sl_pct,"
            "entry_cost_usd,liquidity_usd FROM positions WHERE status='open'"):
        if mint not in price_map or not price_map[mint]:
            # price missing -> increment miss counter
            c.execute("UPDATE positions SET miss_count=COALESCE(miss_count,0)+1 WHERE id=?", (pid,))
            miss = c.execute("SELECT miss_count FROM positions WHERE id=?", (pid,)).fetchone()[0]
            if (miss or 0) >= 2:
                # write‑off after two consecutive missing prices
                net = round(-(size or 0) - (ecost or 0), 0)
                c.execute("UPDATE positions SET status='closed',closed_at=?,exit_usd=0,"
                          "pnl_usd=?,pnl_gross_usd=?,exit_reason='WRITE_OFF', is_writeoff=1 WHERE id=?",
                          (datetime.now(timezone.utc).isoformat(), net,
                           round(-(size or 0), 0), pid))
                # cash already deducted on OPEN; only update realized P&L and total write‑off
                c.execute("UPDATE portfolio SET realized_pnl = realized_pnl + ?, total_writeoff = total_writeoff + ? WHERE id=1",
                          (net, size + ecost))
                # Log write‑off (delta 0, balance unchanged)
                c.execute("SELECT cash_balance FROM portfolio WHERE id=1")
                _bal = c.fetchone()[0]
                c.execute("INSERT INTO cash_log (ts, event, position_id, cash_delta, balance) VALUES (?,?,?,?,?)",
                          (datetime.now(timezone(timedelta(hours=8))).isoformat(), 'WRITE_OFF', pid, 0, _bal))
                closed.append((sym, "WRITE_OFF", net))
                continue
            elif (miss or 0) == 1:
                # Defer single missing price handling until after loop (DROP)
                continue

        px = float(price_map[mint])
        c.execute("UPDATE positions SET miss_count=0 WHERE id=?", (pid,))
        pct = (px - entry) / entry * 100
        reason = None
        if pct >= tp:
            reason = f"take-profit +{pct:.1f}%"
        elif pct <= sl:
            reason = f"stop-loss {pct:.1f}%"
        if reason:
            gross = size * pct / 100
            xcost = trade_cost(size * (1 + pct / 100), liq or 0)
            net = round(gross - (ecost or 0) - xcost, 0)
            c.execute("UPDATE positions SET status='closed',closed_at=?,exit_usd=?,"
                      "pnl_usd=?,pnl_gross_usd=?,exit_cost_usd=?,exit_reason=?, is_writeoff=0 WHERE id=?",
                      (datetime.now(timezone.utc).isoformat(), px, net,
                       round(gross, 0), xcost, reason, pid))
            # Model A: add full proceeds (size + entry_cost + net)
            proceeds = size + ecost + net
            c.execute("UPDATE portfolio SET cash_balance = cash_balance + ?, total_equity = total_equity + ?, "
                      "realized_pnl = realized_pnl + ? WHERE id=1",
                      (proceeds, proceeds, net))
            # Log cash movement (proceeds)
            c.execute("INSERT INTO cash_log (ts, event, position_id, cash_delta, balance) VALUES (?,?,?, ?, (SELECT cash_balance FROM portfolio WHERE id=1))",
                      (datetime.now(timezone(timedelta(hours=8))).isoformat(), 'CLOSE', pid, proceeds))
            # After processing all positions, handle any single‑miss drops.
            drop_rows = c.execute("SELECT id, size_usd, entry_cost_usd FROM positions WHERE status='open' AND miss_count=1").fetchall()
            for drop_id, size_drop, ecost_drop in drop_rows:
                refund = size_drop + ecost_drop
                c.execute("UPDATE positions SET status='closed',closed_at=?,exit_usd=0,"
                          "pnl_usd=0,pnl_gross_usd=0,exit_reason='DROPPED', is_writeoff=0 WHERE id=?",
                          (datetime.now(timezone.utc).isoformat(), drop_id))
                c.execute("UPDATE portfolio SET cash_balance = cash_balance + ?, total_equity = total_equity + ? WHERE id=1",
                          (refund, refund))
                # Log DROP (cash_delta = refund)
                c.execute("INSERT INTO cash_log (ts, event, position_id, cash_delta, balance) VALUES (?,?,?, ?, (SELECT cash_balance FROM portfolio WHERE id=1))",
                          (datetime.now(timezone(timedelta(hours=8))).isoformat(), 'DROP', drop_id, refund))
                closed.append(("DROPPED", refund))
            c.commit()
            c.close()
            return closed


def unrealized(price_map_usd):
    """(unreal_gross, unreal_net, per-mint net dict) for open positions, IDR."""
    price_map = {m: usd_to_idr(p) for m, p in (price_map_usd or {}).items() if p}
    c = conn()
    rows = c.execute("SELECT mint,entry_usd,size_usd,entry_cost_usd,liquidity_usd,"
                     "COALESCE(miss_count,0) FROM positions WHERE status='open'").fetchall()
    c.close()
    ug, un, per = 0.0, 0.0, {}
    for mint, entry, size, ecost, liq, miss in rows:
        if (miss or 0) >= 1 and not price_map.get(mint):
            # hilang dari scout: nilai interim rugi total (-size - biaya masuk)
            gross = -(size or 0)
            net = gross - (ecost or 0)
            ug += gross
            un += net
            per[mint] = round(net, 0)
            continue
        px = price_map.get(mint)
        if not px or not entry:
            continue
        gross = size * (float(px) - entry) / entry
        # assume exit cost at current value
        xcost = trade_cost(size * float(px) / entry, liq or 0)
        net = gross - (ecost or 0) - xcost
        ug += gross
        un += net
        per[mint] = round(net, 0)
    return round(ug, 0), round(un, 0), per


def summary():
    c = conn()
    rows = c.execute("SELECT status,COUNT(*),COALESCE(SUM(pnl_usd),0) FROM positions GROUP BY status").fetchall()
    c.close()
    return rows


def record_signals(decisions, max_open=3):
    """Open paper positions for fresh AMBIL signals.
    v3 rules: max 1 open position per token (skip mints already open),
    max 3 open positions total. Re-entry on a token only after its
    previous position closed (TP/SL evaluated before this runs).
    Bankroll Rp200.000 virtual, Rp40.000/posisi."""
    c = conn()
    already = {r[0] for r in c.execute("SELECT mint FROM positions WHERE status='open'")}
    n_open = c.execute("SELECT COUNT(*) FROM positions WHERE status='open'").fetchone()[0]
    c.close()
    n = 0
    for d in decisions:
        # Apply entry filters before opening a position
        if d.get("signal") != "AMBIL" or d["mint"] in already:
            continue
        if not should_enter(d):
            # Skip tokens that do not satisfy all entry criteria
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


def close_memecoin_positions():
    """Close any open memecoin position after 24h if not already closed.
    Uses the opened_at timestamp to compute age and closes with net loss.
    Marks the row as a write‑off (is_writeoff=1) and updates portfolio accordingly.
    """
    c = conn()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    rows = c.execute("SELECT id, opened_at, size_usd, entry_cost_usd FROM positions WHERE status='open'").fetchall()
    for pid, opened_at, size, ecost in rows:
        opened_dt = datetime.fromisoformat(opened_at)
        if opened_dt <= cutoff:
            gross = - (size or 0)
            net = gross - (ecost or 0)
            c.execute(
                "UPDATE positions SET status='closed',closed_at=?,exit_usd=0,"
                "pnl_usd=?,pnl_gross_usd=?,exit_reason='WRITE_OFF', is_writeoff=1 WHERE id=?",
                (now.isoformat(), net, gross, pid))
            # portfolio adjustments for write‑off (cash and equity decrease, realized P&L records loss, write‑off total increments)
            c.execute("UPDATE portfolio SET cash_balance = cash_balance - ?, total_equity = total_equity - ?, "
                      "realized_pnl = realized_pnl + ?, total_writeoff = total_writeoff + ? WHERE id=1",
                      (size + ecost, size + ecost, net, size + ecost))
    c.commit()
    c.close()

def totals():
    """Return (gross_total, net_total, closed_costs) for closed positions.
    gross_total: sum of pnl_gross_usd (including write-offs where gross is negative size)
    net_total: sum of pnl_usd (net after entry and exit costs)
    closed_costs: sum of entry_cost_usd + exit_cost_usd for all closed positions.
    """
    c = conn()
    row = c.execute("SELECT COALESCE(SUM(pnl_gross_usd),0), COALESCE(SUM(pnl_usd),0), COALESCE(SUM(entry_cost_usd + exit_cost_usd),0) FROM positions WHERE status='closed'").fetchone()
    c.close()
    return row[0] or 0, row[1] or 0, row[2] or 0


def main():
    decs = json.loads((CACHE / "decision_latest.json").read_text())
    n = record_signals(decs)
    print(f"[ledger] {n} paper positions opened ({len(decs)} signals)")
    for status, cnt, pnl in summary():
        print(f"  {status}: {cnt} pos, P&L Rp{pnl:,.0f} (SIMULASI)")


if __name__ == "__main__":
    main()
