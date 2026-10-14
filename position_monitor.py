# position_monitor.py – runs every 15 min to evaluate open paper positions
import json
import pathlib
import sqlite3
from datetime import datetime, timezone

# Re‑use existing functions from paper_ledger via import
import importlib.util, sys
ROOT = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('ledger', str(ROOT / 'paper_ledger.py'))
ledger = importlib.util.module_from_spec(spec)
sys.modules['ledger'] = ledger
spec.loader.exec_module(ledger)

CACHE = ROOT / 'cache'
DECISIONS_PATH = CACHE / 'decision_latest.json'

def fetch_current_prices():
    """Return a dict {mint: price_usd_now} extracted from the latest decision JSON.
    This mimics a batch RPC call to get current market data.
    """
    try:
        decisions = json.loads(DECISIONS_PATH.read_text())
    except Exception:
        return {}
    price_map = {}
    for d in decisions:
        mint = d.get('mint')
        price = None
        # Prefer chart.price_usd_now, fall back to price_usd
        if d.get('chart') and d['chart'].get('price_usd_now'):
            price = d['chart']['price_usd_now']
        elif d.get('price_usd'):
            price = d['price_usd']
        if mint and price:
            try:
                price_map[mint] = float(price)
            except Exception:
                pass
    return price_map

def run_monitor():
    """Main entry point – called by cron or manually.
    It loads current prices, then delegates to ledger.mark_to_market which
    handles TP, SL, and write‑off (missing price 2×) using the existing logic.
    """
    price_map = fetch_current_prices()
    if not price_map:
        print('No price data available – monitor idle')
        return
    closed = ledger.mark_to_market(price_map)
    print(f'Position monitor closed {len(closed)} positions')
    for sym, reason, pnl in closed:
        print(f'  {sym}: {reason}, PnL={pnl}')

if __name__ == '__main__':
    run_monitor()
