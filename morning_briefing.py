"""Morning briefing runner: full paper-trading pipeline -> daily report file + briefing text.

SIMULATION ONLY. No wallets, no private keys, no live orders.
Steps: scout -> news -> sentiment -> charts -> whale -> risk -> decision ->
mark-to-market open paper positions -> record new AMBIL signals ->
write reports/YYYY-MM-DD.md -> git commit+push -> print briefing (stdout).
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
START = 100.0
AGENTS = ["scout", "news", "sentiment", "charts", "whale", "risk", "decision"]
NAMES = {"scout": "Scout", "news": "News", "sentiment": "Sentiment",
         "charts": "Charts", "whale": "Whale", "risk": "Risk", "decision": "Decision"}
WHALE_ICON = {"akumulasi": "🐋 akumulasi", "distribusi": "🐋 distribusi",
              "netral": "netral"}


def sh(args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=900, cwd=str(ROOT), env=env)


def money(x):
    return ("$%g" % x) if x else "-"


def signed(x):
    return f"${x:+.2f}"


def main():
    day = datetime.now().strftime("%Y-%m-%d")
    status = {}
    for a in AGENTS:
        r = sh([PY, f"{a}.py"])
        status[a] = (r.returncode == 0)
        if r.returncode != 0:
            print(f"[{a}] FAILED:\n{(r.stderr or '')[-800:]}", file=sys.stderr)

    sys.path.insert(0, str(ROOT))
    import paper_ledger as ledger

    dec_path = ROOT / "cache" / "decision_latest.json"
    if not dec_path.exists():
        dec_path = ROOT / "cache" / "whale_latest.json"
    decs = json.loads(dec_path.read_text())
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
    opens = c.execute("SELECT symbol,entry_usd,size_usd,mint FROM positions "
                      "WHERE status='open' ORDER BY id").fetchall()
    realized, n_closed = c.execute("SELECT COALESCE(SUM(pnl_usd),0),COUNT(*) "
                                   "FROM positions WHERE status='closed'").fetchone()
    all_closed = c.execute("SELECT symbol,entry_usd,exit_usd,pnl_usd,pnl_gross_usd,exit_reason "
                           "FROM positions WHERE status='closed' ORDER BY id").fetchall()
    open_costs = c.execute("SELECT COALESCE(SUM(entry_cost_usd),0) FROM positions "
                           "WHERE status='open'").fetchone()[0]
    c.close()

    open_lines = []
    for sym, entry, size, mint in opens:
        now = prices.get(mint)
        pnl = per_mint.get(mint, 0.0)
        now_s = money(now) if now else "n/a (hilang dari scout)"
        open_lines.append(f"- {sym} | entry {money(entry)} → now {now_s} | P&L {signed(pnl)}")

    gross_total = rg + ug
    net_total = rn + un
    bankroll = START + net_total
    pct = net_total / START * 100

    sig_lines = []
    for d in decs:
        ch = d.get("chart") or {}
        w = d.get("whale") or {}
        wtxt = WHALE_ICON.get(w.get("status"), w.get("status") or "netral")
        sig_lines.append(f"- {d.get('signal')} {d.get('symbol')} | skor {d.get('total_score')} "
                         f"| risk {d.get('risk_1_10')}/10 | pola {ch.get('pattern')} "
                         f"| liq ${(d.get('liquidity_usd') or 0):,.0f} | {wtxt}")

    agen = " | ".join(f"{NAMES[a]} {'✅' if status[a] else '❌'}" for a in AGENTS)

    msg = (f"📊 LAPORAN HARIAN — {day}\n"
           f"💰 Bankroll virtual: ${bankroll:.2f} (awal $100)\n"
           f"📈 P&L total: {signed(net_total)} ({pct:+.2f}%) — "
           f"kotor {signed(gross_total)} vs bersih {signed(net_total)} (setelah biaya)\n"
           f"\n🟢 POSISI TERBUKA ({len(opens)}):\n"
           + ("\n".join(open_lines) if open_lines else "- tidak ada posisi terbuka") + "\n"
           f"\n🎯 SINYAL HARI INI ({len(decs)} kandidat):\n"
           + "\n".join(sig_lines) + "\n"
           f"\n🤖 STATUS AGEN: {agen}")

    rep = ROOT / "reports" / f"{day}.md"
    rep.parent.mkdir(exist_ok=True)
    det = [f"# Laporan Harian — {day}", "",
           f"Bankroll virtual: ${bankroll:.2f} (awal $100)",
           f"P&L kotor {signed(gross_total)} vs bersih {signed(net_total)} ({pct:+.2f}%) "
           f"= realisasi bersih {signed(rn)} (biaya closed {signed(-closed_costs)}) "
           f"+ unrealisasi bersih {signed(un)} (biaya open {signed(-(open_costs or 0))}).",
           f"Posisi: {len(opens)} terbuka, {n_closed} tertutup. "
           f"Sinyal baru dicatat: {n_new}.", ""]
    if newly_closed:
        det.append("Posisi tertutup pada run ini:")
        det += [f"- {s}: {r} (P&L bersih {signed(p)})" for s, r, p in newly_closed] + [""]
    if all_closed:
        det.append("Riwayat posisi tertutup:")
        det += [f"- {s}: entry {money(e)} -> exit {money(x)} | bersih {signed(p)} "
                f"(kotor {signed(g)}) | {r}"
                for s, e, x, p, g, r in all_closed] + [""]
    det.append("Keputusan hari ini:")
    for d in decs:
        w = d.get("whale") or {}
        det.append(f"- [{d.get('signal')}] {d.get('symbol')} "
                   f"skor={d.get('total_score')} risk={d.get('risk_1_10')}/10 "
                   f"pola={(d.get('chart') or {}).get('pattern')} "
                   f"liq=${(d.get('liquidity_usd') or 0):,.0f} "
                   f"whale={w.get('status')} ({w.get('concentration')})")
        det.append(f"  alasan: {'; '.join(d.get('decision_reasons', []))}")
        if w.get("note"):
            det.append(f"  whale: {w.get('note')}")
    det += ["", "Biaya: gas $0.05/tx + fee DEX 0.25% + slippage 2% (1% jika liq > $100k), "
            "dipotong tiap buka & tutup posisi.",
            "SIMULASI EDUKASI — bukan nasihat finansial. Tanpa uang asli."]
    rep.write_text("\n".join(det))

    push_note = ""
    try:
        sh(["git", "add", f"reports/{day}.md"])
        sh(["git", "commit", "-m", f"Morning briefing {day}"])
        r = sh(["git", "push", "origin", "main"])
        if r.returncode != 0:
            push_note = f"\n\n(push gagal: {(r.stderr or '')[-200:]})"
    except Exception as e:
        push_note = f"\n\n(push gagal: {e})"

    print(msg + push_note)


if __name__ == "__main__":
    main()
