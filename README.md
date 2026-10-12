# AI Trading Bot Team (Paper Trading Research)

A 7-agent pipeline that scouts newly-listed Solana meme coins and produces
**simulated** paper-trading signals. Every "buy" is a row in a local SQLite
file with virtual prices — there is no live-trading code in this project.

> ⚠️ **DISCLAIMER — EDUCATIONAL SIMULATION ONLY, NOT FINANCIAL ADVICE.**
> All signals, scores, and P&L figures are simulated for research and learning.
> Meme coins are extremely volatile; most go to zero. Never trade with money
> you cannot afford to lose. This project contains no wallets, no private keys,
> and no order-execution code by design.

## Architecture — 7 agents

```text
Scout → News → Sentiment → Charts → Whale → Risk → Decision → Paper Ledger → Report
```

| # | Agent | File | Input → Output |
|---|---|---|---|
| 1 | Scout | `scout.py` | DexScreener latest profiles → Solana candidates filtered by min liquidity ($10k) & max pair age (14d) |
| 2 | News | `news.py` | CoinGecko free tier contract lookup → listed/unlisted enrichment (unlisted = higher risk, not an error) |
| 3 | Sentiment | `sentiment.py` | RugCheck free report (top holders %, mint/freeze authority) + DexScreener txns/volume → holder concentration + hype 0-100 |
| 4 | Charts | `charts.py` | DexScreener price-change ladder (m5/h1/h6/h24) → momentum/volatility → named pattern + chart score |
| 5 | Whale | `whale.py` | Top-20 holder snapshots (RugCheck, RPC fallback) vs previous day → akumulasi / distribusi / netral + concentration; stored in `data/whale.db` |
| 6 | Risk | `risk.py` | Liquidity, listing, holder concentration, whale concentration/distribution, volatility, age → transparent risk score 1-10 with reasons |
| 7 | Decision | `decision.py` | `0.35·hype + 0.35·chart + 0.30·(100 − risk·10)` → **AMBIL** (paper buy) / **LEWATI** + written reasons |

> 🐋 **A note on whale data:** a wallet address is not an identity. A large
> holder can be the project team, a CEX hot wallet, a market maker, or a
> bandar — we cannot know which. Whale signals are *supporting data* for risk
> scoring (high concentration adds risk points), never a verdict.

Supporting modules:

- `paper_ledger.py` — **replaces a live order manager.** Simulated positions in
  `data/paper.db` (virtual $100 bankroll, $20/position, max 5 open,
  TP +25% / SL −15%). Real-cost model deducted on every open AND close:
  Solana gas $0.05/tx + DEX fee 0.25% + slippage 2% (1% if liquidity > $100k).
  P&L is reported gross vs net (after costs).
- `report.py` — daily console report: signals + simulated P&L.
- `morning_briefing.py` — scheduled runner: full pipeline, mark-to-market,
  record signals, write `reports/YYYY-MM-DD.md`, commit + push.

## Data sources (all free, no keys)

- DexScreener free API (new profiles, pairs, price-change ladder)
- CoinGecko free tier (contract lookup; rate-limit friendly ~1 req/7s)
- RugCheck free report API (holder distribution, authorities)
- Solana public RPC (fallback for holder data; frequently rate-limited)

All API responses are cached as JSON in `cache/`; ledger lives in `data/paper.db`.

## How to run

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
python scout.py       # 1. find candidates (live DexScreener data)
python news.py        # 2. CoinGecko enrichment
python sentiment.py   # 3. holders + hype
python charts.py      # 4. trend/volatility/patterns
python whale.py       # 5. holder snapshots + akumulasi/distribusi
python risk.py        # 6. risk scores (incl. whale concentration)
python decision.py    # 7. AMBIL / LEWATI
python paper_ledger.py  # record paper positions for AMBIL signals
python report.py        # daily summary
# or everything at once (incl. reports/*.md + push):
python morning_briefing.py
```

## Example output

```text
📊 LAPORAN HARIAN — 2026-10-12
💰 Bankroll virtual: $92.75 (awal $100)
📈 P&L total: $-7.25 (-7.25%) — kotor $-5.88 vs bersih $-7.25 (setelah biaya)

🟢 POSISI TERBUKA (5):
- CLAUDINU | entry $0.000275 → now $0.0003021 | P&L $+0.93

🎯 SINYAL HARI INI (6 kandidat):
- AMBIL CLAUDINU | skor 67.5 | risk 5/10 | pola parabolic_hot | liq $46,315 | netral

🤖 STATUS AGEN: Scout ✅ | News ✅ | Sentiment ✅ | Charts ✅ | Whale ✅ | Risk ✅ | Decision ✅
```

## Rules & limits

- Paper trading / signal research **only**. No real money, no wallets, no private keys, no live orders — enforced by containing zero execution code.
- All free APIs are rate-limited and handled gracefully: per-token delays
  (DexScreener ~1-1.5s, CoinGecko ~7s, RugCheck/RPC ~2s), retries with backoff,
  and unavailable data recorded as "tak tersedia" (adds risk points) instead of
  crashing the pipeline.
- DexScreener's OHLC chart endpoint requires auth, so Charts uses the public price-change ladder; thresholds in `decision.py` (`THRESHOLD = 55`, `MAX_RISK = 7`) are tunable.

## Tech

Python · requests · pandas · SQLite
