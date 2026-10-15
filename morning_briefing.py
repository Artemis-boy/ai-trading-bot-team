"""Briefing runner (v3, 4x daily): full paper-trading pipeline -> per-run report + briefing text.

SIMULATION ONLY. No wallets, no private keys, no live orders.
Pipeline v2: scout -> news -> sentiment -> charts -> risk -> decision ->
whale -> paper_ledger -> report.
Whale runs after decision; risk scores use the previous whale snapshot
(file-per-stage, degrades gracefully). Display merges today's whale by mint.
Steps: run agents -> mark-to-market (close TP/SL hits FIRST) ->
record new AMBIL (max 1/token, max 3 total, re-entry only after close) ->
write reports/YYYY-MM-DD-HHmm.md -> git commit+push -> print briefing (stdout).
Runs 4x daily (07:00, 12:00, 17:00, 22:00 WITA).
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("C:/Users/ikraa/Documents/ai-trading-bot-team")
PY = str(ROOT / "venv" / "Scripts" / "python.exe")
START = 200_000  # Rp
AGENTS = ["scout", "news", "sentiment", "charts", "risk", "decision", "whale"]
EXTRA_STEPS = ["paper_ledger", "report"]
NAMES = {"scout": "Scout", "news": "News", "sentiment": "Sentiment",
         "charts": "Charts", "risk": "Risk", "decision": "Decision", "whale": "Whale"}
WHALE_ICON = {"akumulasi": "🐋 akumulasi", "distribusi": "🐋 distribusi",
              "netral": "🐋 netral"}


def sh(args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=900, cwd=str(ROOT), env=env)


def money(x):
    return (f"Rp{x:,.0f}") if x else "-"


def rp(x):
    if x is None:
        return "-"
    return f"Rp{float(x):,.0f}"


def rp_signed(x):
    x = float(x or 0)
    sign = "+" if x >= 0 else "-"
    return f"{sign}Rp{abs(x):,.0f}"


def signed(x):
    x = float(x or 0)
    sign = "+" if x >= 0 else "-"
    return f"{sign}Rp{abs(x):,.0f}"


def main():
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y-%m-%d-%H%M")
    label = now.strftime("%Y-%m-%d %H:%M")
    status = {}
    for a in AGENTS + EXTRA_STEPS:
        r = sh([PY, f"{a}.py"])
        status[a] = (r.returncode == 0)
        if r.returncode != 0:
            print(f"[{a}] FAILED:\n{(r.stderr or '')[-800:]}", file=sys.stderr)

    sys.path.insert(0, str(ROOT))
    import paper_ledger as ledger

    dec_path = ROOT / "cache" / "decision_latest.json"
    if not dec_path.exists():
        dec_path = ROOT / "cache" / "risk_latest.json"
    decs = json.loads(dec_path.read_text())
    whale_path = ROOT / "cache" / "whale_latest.json"
    whale_by_mint = {}
    if whale_path.exists():
        try:
            for w in json.loads(whale_path.read_text()):
                whale_by_mint[w.get("mint")] = w.get("whale") or {}
        except Exception as e:
            print(f"[briefing] whale cache unreadable: {e}", file=sys.stderr)
    for d in decs:
        if not d.get("whale") and d.get("mint") in whale_by_mint:
            d["whale"] = whale_by_mint[d["mint"]]
    prices = {}
    for d in decs:
        try:
            prices[d["mint"]] = float((d.get("chart") or {}).get("price_usd_now")
                                     or d.get("price_usd"))
        except (TypeError, ValueError):
            pass

    newly_closed = ledger.mark_to_market(prices)
    n_new = ledger.record_signals(decs)
    ug, un, per_mint = ledger.unrealized(prices)
    rg, rn, closed_costs = ledger.totals()

    c = sqlite3.connect(ROOT / "data" / "paper.db")
    opens = c.execute("SELECT symbol,entry_usd,size_usd,mint,COALESCE(miss_count,0) "
                      "FROM positions WHERE status='open' ORDER BY id").fetchall()
    realized, n_closed = c.execute("SELECT COALESCE(SUM(pnl_usd),0),COUNT(*) "
                                   "FROM positions WHERE status='closed'").fetchone()
    all_closed = c.execute("SELECT symbol,entry_usd,exit_usd,pnl_usd,pnl_gross_usd,exit_reason "
                           "FROM positions WHERE status='closed' ORDER BY id").fetchall()
    open_costs = c.execute("SELECT COALESCE(SUM(entry_cost_usd),0) FROM positions "
                           "WHERE status='open'").fetchone()[0]
    c.close()

    open_lines, miss_lines = [], []
    for sym, entry, size, mint, miss in opens:
        now_usd = prices.get(mint)
        now = ledger.usd_to_idr(now_usd) if now_usd else None
        pnl = per_mint.get(mint, 0.0)
        if now is None:
            miss_lines.append(f"- {sym} | entry {rp(entry)} | "
                              f"⚠️ HILANG (kemungkinan rug) | dinilai {rp_signed(pnl)}")
        else:
            open_lines.append(f"- {sym} | entry {rp(entry)} → now {rp(now)} | P&L {rp_signed(pnl)}")

    gross_total = rg + ug
    conn = ledger.conn()
    # Retrieve cash_balance and topup_history (the JSON list of top‑ups) from the portfolio table.
    # Retrieve cash_balance and full topup_history JSON (column index 6)
    row = conn.execute('SELECT cash_balance, total_equity, topup_history FROM portfolio WHERE id=1').fetchone()
    cash_balance = row[0]
    total_equity = row[1]
    topup_history_json = row[2]
    # Keep total_equity in sync with cash_balance when there are no open positions
    if cash_balance != total_equity:
        conn.execute('UPDATE portfolio SET total_equity = ? WHERE id = 1', (cash_balance,))
        conn.commit()

    # total deposit = initial 200 000 plus sum of all top‑ups recorded
    try:
        topups = json.loads(topup_history_json)
        total_topup = sum(item.get('amount', 0) for item in topups)
    except Exception:
        total_topup = 0
    total_deposit = 200_000 + total_topup
    bankroll = cash_balance
    net_total = rn + un
    pct = net_total / total_deposit * 100

    sig_lines = []
    for d in decs:
        ch = d.get("chart") or {}
        w = d.get("whale") or {}
        wtxt = WHALE_ICON.get(w.get("status"), w.get("status") or "netral")
        liq_idr = (d.get("liquidity_usd") or 0) * ledger.USD_IDR
        sig_lines.append(f"- {d.get('signal')} {d.get('symbol')} | skor {d.get('total_score')} "
                         f"| risk {d.get('risk_1_10')}/10 | pola {ch.get('pattern')} "
                         f"| liq Rp{liq_idr:,.0f} | {wtxt}")

    agen = " | ".join(f"{NAMES[a]} {'✅' if status[a] else '❌'}" for a in AGENTS)

    msg = (f"📊 LAPORAN — {label}\n"
           f"💰 Bankroll virtual: Rp{bankroll:,.0f} (awal Rp{total_deposit:,.0f})\n"
           f"📈 P&L total: {rp_signed(net_total)} ({pct:+.2f}%) — "
           f"kotor {rp_signed(gross_total)} vs bersih {rp_signed(net_total)} (setelah biaya)\n"
           f"\n🟢 POSISI TERBUKA ({len(open_lines)}/3):\n"
           + ("\n".join(open_lines) if open_lines else "- tidak ada posisi terbuka") + "\n"
           + (f"\n⚠️ POSISI HILANG ({len(miss_lines)}):\n" + "\n".join(miss_lines) + "\n"
              if miss_lines else "")
           + f"\n🎯 SINYAL RUN INI ({len(decs)} kandidat):\n"
           + "\n".join(sig_lines) + "\n"
           f"\n🤖 STATUS AGEN: {agen}")

    rep = ROOT / "reports" / f"{stamp}.md"
    rep.parent.mkdir(exist_ok=True)
    det = [f"# Laporan — {label}", "",
           f"Bankroll virtual: Rp{bankroll:,.0f} (awal Rp{total_deposit:,.0f})",
           f"P&L kotor {rp_signed(gross_total)} vs bersih {rp_signed(net_total)} ({pct:+.2f}%) "
           f"= realisasi bersih {rp_signed(rn)} (biaya closed {rp_signed(-closed_costs)}) "
           f"+ unrealisasi bersih {rp_signed(un)} (biaya open {rp_signed(-(open_costs or 0))}).",
           f"Posisi: {len(open_lines)} terbuka bernilai, "
           f"{len(miss_lines)} hilang (dinilai rugi total), {n_closed} tertutup. "
           f"Sinyal baru dicatat: {n_new}.", ""]
    if miss_lines:
        det.append("⚠️ POSISI HILANG (tak ada di scout, dinilai rugi total):")
        det += miss_lines + [""]
    if newly_closed:
        det.append("Posisi tertutup pada run ini:")
        det += [f"- {s}: {r} (P&L bersih {rp_signed(p)})" for s, r, p in newly_closed] + [""]
    if all_closed:
        det.append("Riwayat posisi tertutup:")
        det += [f"- {s}: entry {rp(e)} -> exit {rp(x)} | bersih {rp_signed(p)} "
                f"(kotor {rp_signed(g)}) | {r}"
                for s, e, x, p, g, r in all_closed] + [""]
    det.append("Keputusan hari ini:")
    for d in decs:
        w = d.get("whale") or {}
        det.append(f"- [{d.get('signal')}] {d.get('symbol')} "
                   f"skor={d.get('total_score')} risk={d.get('risk_1_10')}/10 "
                   f"pola={(d.get('chart') or {}).get('pattern')} "
                   f"liq=Rp{(d.get('liquidity_usd') or 0) * ledger.USD_IDR:,.0f} "
                   f"whale={w.get('status')} ({w.get('concentration')})")
        det.append(f"  alasan: {'; '.join(d.get('decision_reasons', []))}")
        if w.get("note"):
            det.append(f"  whale: {w.get('note')}")
    det += ["", "Biaya model Axiom: fee Axiom 0,95% + priority fee 0,001 SOL "
            "(Rp2.000, kurs 1 SOL = Rp2.000.000) + fee DEX 0,25% + slippage 2% "
            "(1% jika liq > $100k) — dipotong tiap buka & tutup posisi.",
            "SIMULASI EDUKASI — bukan nasihat finansial. Tanpa uang asli."]
    rep.write_text("\n".join(det))

    push_note = ""
    try:
        sh(["git", "add", f"reports/{stamp}.md"])
        sh(["git", "commit", "-m", f"Briefing {stamp}"])
        r = sh(["git", "push", "origin", "main"])
        if r.returncode != 0:
            push_note = f"\n\n(push gagal: {(r.stderr or '')[-200:]})"
    except Exception as e:
        push_note = f"\n\n(push gagal: {e})"

    print(msg + push_note)


if __name__ == "__main__":
    main()
