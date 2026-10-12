"""Agent 7 - WHALE: daily top-holder snapshots + accumulation/distribution detection.

Sources: RugCheck free report (topHolders, no key) first; Solana public RPC
getTokenLargestAccounts as fallback. Rate-limit safe: per-token try/except,
courtesy delays, never crash — unavailable data is recorded, not fatal.

Snapshots: data/whale.db -> snapshots(day, mint, symbol, top10_pct, holders_json).
Detection vs previous snapshot for same mint:
  WHALE MASUK   : new address >=1% supply in current top-20
  WHALE KELUAR  : address >=1% in previous top-20 gone from current top-20
  akumulasi     : top10 share up >5pts (or whale masuk)
  distribusi    : top10 share down >5pts (or whale keluar)
  netral        : otherwise / first snapshot
Concentration: top10 >=70% TINGGI, >=50% SEDANG, else RENDAH.

NOTE on identity: a wallet address is NOT an identity — a big holder can be
the team, a CEX hot wallet, a market maker, or a bandar. This module is
supporting data for risk scoring, never a verdict.

Paper-trading research only. No wallets, no private keys, no live orders.
"""
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
DB = ROOT / "data" / "whale.db"
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens"
RPCS = ["https://api.mainnet-beta.solana.com"]
TIMEOUT = 25
DELAY_S = 2
CONC_HIGH = 70.0
CONC_MED = 50.0
WHALE_PCT = 1.0
DELTA_PTS = 5.0


def db():
    DB.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots(
      day TEXT, mint TEXT, symbol TEXT, top10_pct REAL, holders_json TEXT,
      PRIMARY KEY(day, mint))""")
    return c


def rugcheck_top(mint):
    r = requests.get(f"{RUGCHECK}/{mint}/report", timeout=TIMEOUT,
                     headers={"User-Agent": "ai-trading-bot-team/research"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("topHolders") or []


def rpc_top(mint):
    body = {"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts",
            "params": [mint]}
    last = None
    for url in RPCS:
        try:
            r = requests.post(url, json=body, timeout=TIMEOUT,
                              headers={"Content-Type": "application/json"})
            if r.status_code == 429:
                raise RuntimeError("429 rate-limited")
            r.raise_for_status()
            return r.json()["result"]["value"]
        except Exception as e:
            last = e
            time.sleep(3)
    raise RuntimeError(f"RPC unavailable: {last}")


def fetch_top20(mint):
    """Return (holdings, source) where holdings = [(address, pct)]."""
    try:
        holders = rugcheck_top(mint)
        if holders:
            rows = sorted(((h.get("address", "?"), float(h.get("pct", 0)))
                           for h in holders), key=lambda x: -x[1])[:20]
            return rows, "rugcheck"
    except Exception as e:
        err_rc = str(e)
    else:
        err_rc = "empty"
    try:
        accts = rpc_top(mint)
        total = sum(float(a.get("uiAmount", 0) or 0) for a in accts) or 1
        rows = sorted(((a.get("address", "?"),
                        float(a.get("uiAmount", 0) or 0) / total * 100)
                       for a in accts), key=lambda x: -x[1])[:20]
        return rows, "rpc"
    except Exception as e:
        return None, f"tak tersedia (rugcheck: {err_rc}; rpc: {e})"


def prev_snapshot(mint, today):
    c = db()
    row = c.execute("SELECT day, top10_pct, holders_json FROM snapshots "
                    "WHERE mint=? AND day<? ORDER BY day DESC LIMIT 1",
                    (mint, today)).fetchone()
    c.close()
    if not row:
        return None
    return {"day": row[0], "top10_pct": row[1],
            "holders": json.loads(row[2])}


def top10(rows):
    return round(sum(p for _, p in rows[:10]), 2)


def detect(cur_rows, prev):
    cur10 = top10(cur_rows)
    if prev is None:
        conc = "tinggi" if cur10 >= CONC_HIGH else ("sedang" if cur10 >= CONC_MED else "rendah")
        return {"status": "netral", "concentration": conc, "top10_pct": cur10,
                "note": "snapshot awal, belum ada pembanding"}
    cur_map = dict(cur_rows)
    prev_map = {a: p for a, p in prev["holders"]}
    masuk = [a for a, p in cur_map.items() if p >= WHALE_PCT and a not in prev_map]
    keluar = [a for a, p in prev_map.items() if p >= WHALE_PCT and a not in cur_map]
    delta = round(cur10 - prev["top10_pct"], 2)
    conc = "tinggi" if cur10 >= CONC_HIGH else ("sedang" if cur10 >= CONC_MED else "rendah")
    if masuk and not keluar:
        status, note = "akumulasi", f"WHALE MASUK: {len(masuk)} wallet baru >=1% (top10 {delta:+.1f}pts)"
    elif keluar and not masuk:
        status, note = "distribusi", f"WHALE KELUAR: {len(keluar)} wallet >=1% hilang (top10 {delta:+.1f}pts)"
    elif masuk and keluar:
        status, note = "netral", f"rotasi: {len(masuk)} masuk, {len(keluar)} keluar (top10 {delta:+.1f}pts)"
    elif delta > DELTA_PTS:
        status, note = "akumulasi", f"top10 naik {delta:+.1f}pts sejak {prev['day']}"
    elif delta < -DELTA_PTS:
        status, note = "distribusi", f"top10 turun {delta:+.1f}pts sejak {prev['day']}"
    else:
        status, note = "netral", f"top10 stabil {delta:+.1f}pts sejak {prev['day']}"
    return {"status": status, "concentration": conc, "top10_pct": cur10,
            "top10_prev": prev["top10_pct"], "whale_in": len(masuk),
            "whale_out": len(keluar), "note": note}


def analyze(cands, today=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    c = db()
    out = []
    for d in cands:
        sym = d.get("symbol")
        rows, source = fetch_top20(d["mint"])
        if rows is None:
            w = {"status": "netral", "concentration": "tak tersedia",
                 "top10_pct": None, "note": source, "unavailable": True}
            print(f"  {sym}: holder data tak tersedia")
        else:
            prev = prev_snapshot(d["mint"], today)
            w = detect(rows, prev)
            w["source"] = source
            c.execute("INSERT OR REPLACE INTO snapshots(day,mint,symbol,top10_pct,holders_json)"
                      " VALUES(?,?,?,?,?)",
                      (today, d["mint"], sym, w["top10_pct"], json.dumps(rows)))
            print(f"  {sym}: {w['status']} | konsentrasi {w['concentration']} "
                  f"(top10 {w['top10_pct']}%) [{source}]")
        out.append({**d, "whale": w})
        time.sleep(DELAY_S)
    c.commit()
    c.close()
    return out


def main():
    src = CACHE / "charts_latest.json"
    cands = json.loads(src.read_text())
    print(f"[whale] analyzing {len(cands)} candidates...")
    out = analyze(cands)
    (CACHE / "whale_latest.json").write_text(json.dumps(out, indent=2))
    print("[whale] done -> cache/whale_latest.json + data/whale.db")
    return out


if __name__ == "__main__":
    main()
