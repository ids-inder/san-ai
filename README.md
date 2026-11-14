# San Arb

A minimum-capital arbitrage scanner and **auto-trade engine** for Indian
markets, wired for **FYERS**. It watches the handful of arbitrage-style edges
that are reachable with a small account, decides whether an edge is **real after
costs**, and — once armed and funded — places the trade.

```
scan  →  fair-value adjust  →  cost model  →  risk gate  →  execute
```

It is **safe by default**: it runs in PAPER (simulation) mode and will not place
a single real order until you explicitly arm it *and* supply FYERS credentials.

---

## The honest part (read this first)

You asked for **full auto-trade with under ₹10,000**. Here is the truth the tool
is built around, so it never pretends otherwise:

- With ~₹10k, the only edges that qualify are the **international-ETF
  discount-catch** and the **SGB discount-catch**. Genuinely auto-tradeable arb
  like cash-futures needs ₹5 lakh+.
- These edges are **occasional**, not always-on. Most scans correctly find
  **nothing to do** — that's the gate working, not a bug.
- An SGB unit costs ~₹9,000, which bumps against a ₹10k account's per-trade cap,
  so in practice the ₹10k sweet spot is the **cheap ETF units**. (Raise
  `SANARB_PER_TRADE_CAP_PCT` to `1.0` if you want to buy a single SGB unit at the
  cost of diversification.)
- **Live auto-execution via FYERS legally requires an API app registered under
  SEBI's retail-algo framework** (effective 1 Apr 2026). The software can place
  orders; FYERS will reject an unregistered app at the order endpoint. The tool
  surfaces that rather than hiding it.

So: run it on **paper** today, watch it behave, grow the capital, register the
algo app, then arm it. Nothing here will silently risk your money.

---

## What it trades

| Strategy | What it looks for | Entry rule |
|---|---|---|
| `etf_premium` | India-listed international ETFs (MON100, Mirae FANG+, Hang Seng BeES) dislocated from **fair value** | **BUY only on a genuine discount** beyond `ETF_DISCOUNT_ENTRY`; **AVOID/harvest** above `ETF_PREMIUM_WARN`. Never buys a premium. |
| `sgb_discount` | Sovereign Gold Bonds trading **below gold fair value** on the exchange | BUY beyond `SGB_DISCOUNT_ENTRY`; LIMIT-only (thin book). |

The ETF "fair value" rolls the **stale foreign-close NAV** forward by the
overnight index move and the rupee, so **time-zone drift is not mistaken for an
edge** — the single most important correction a naïve "price vs NAV" app gets
wrong.

Cash-futures and buyback strategies are deliberately **left out** (they need far
more capital). Add them in `sanarb/strategies/registry.py` when the account
grows.

---

## Safety model

Every order must survive **all** gates, in order:

1. **Strategy** emits a BUY/SELL only if the fair-value-adjusted, **cost-netted**
   edge is positive. Premiums are never bought.
2. **Risk gate** (`engine/risk.py`) — the only place money-at-risk is decided:
   - position size ≤ `per_trade_cap`, ≤ portfolio headroom, ≤ available cash;
   - **liquidity**: reject if spread > `MAX_SPREAD_PCT`; never take more than
     `MAX_BOOK_PARTICIPATION` of resting size;
   - **LIMIT-only** on thin instruments (market orders blocked);
   - `MAX_OPEN_POSITIONS`, cash reserve respected.
3. **Live gate** (`engine/executor.py`) — real orders only when
   `SANARB_MODE=live` **AND** `SANARB_ARM_LIVE=1`. Otherwise the PaperBroker is
   used and the FYERS SDK is never even imported.
4. **Audit** — every scan, veto, and order is appended to
   `.sanarb/audit.log.jsonl`.

---

## Install

Core runs on the **standard library only** — no dependencies for paper mode or
tests.

```bash
pip install -e .            # core (paper mode, scanner, CLI)
pip install -e ".[live]"    # adds fyers-apiv3 for live data/trading
pip install -e ".[dev]"     # adds pytest
```

Python 3.10+.

---

## Usage

```bash
sanarb scan --mock          # one pass on bundled sample data, print signals
sanarb run --mock           # scan + PAPER execute one pass
sanarb run --mock --loop    # continuous, every SANARB_SCAN_INTERVAL_SEC
sanarb positions            # cash + open positions (paper or live)
sanarb ledger -n 30         # tail the audit log
sanarb config               # effective settings (secrets redacted)
```

Configure via environment or a `.env` file — see `.env.example`.

### Example scan

```
  [BUY ] Nippon Hang Seng BeES          edge  +2.7%  ref 341.36
        Trading 3.3% BELOW fair value; redemption arm supports the floor.
  [AVOID] Motilal Oswal Nasdaq-100 ETF   edge -30.2% ref 261.04
        Trading 30.2% ABOVE fair value - buying is overpaying for NAV exposure.
```

---

## Going live (FYERS)

1. Create an API app at <https://myapi.fyers.in/> — **registered under the SEBI
   retail-algo framework** so it can place orders.
2. Put `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI` in `.env`.
3. Authenticate (tokens expire ~daily):
   ```bash
   sanarb auth-url                 # open the printed URL, log in
   sanarb auth-token <auth_code>   # prints FYERS_ACCESS_TOKEN=...
   ```
   Put the token in `.env`.
4. Dry-run against **live data** but paper fills: leave `SANARB_MODE=paper`.
   Now `scan`/`run` use FYERS quotes, orders stay simulated.
5. **Arm live** only when you mean it:
   ```bash
   SANARB_MODE=live SANARB_ARM_LIVE=1 sanarb run --loop
   ```
   A red banner confirms the mode before anything is placed.

---

## Architecture

```
sanarb/
  config.py            all knobs; env-overridable; the live gate lives here
  models.py            Quote / Signal / Order / Position value objects
  store.py             JSON ledger + JSONL audit log
  fyers_auth.py        FYERS v3 login URL + token exchange
  providers/           market data: base, mock (offline), fyers (lazy)
  strategies/          pure (data -> signals): etf_premium, sgb_discount
  engine/
    fairvalue.py       stale-NAV roll-forward + premium math
    scanner.py         run all strategies, rank
    risk.py            the risk gate (position sizing + liquidity)
    executor.py        risk -> broker, with the live/paper gate
    runner.py          the scan->execute loop
  broker/              paper (simulated) and fyers (live, lazy, gated)
  cli.py               argparse CLI
```

Strategies are **pure** and know nothing about the account; all money decisions
are in `risk.py`; only `broker/fyers.py` can move real money, and only through
the executor's live gate.

## Tests

```bash
python -m pytest -q
```

Covers the fair-value math (incl. the time-zone false-discount case), both
strategies, every risk-gate rule, end-to-end paper execution, and the live-gate
invariant (live mode without arming never touches FYERS).

## Disclaimer

This is trading software for personal use. Arbitrage edges are small, occasional,
and can be erased by costs, slippage, taxes, or a bad fill. Nothing here is
investment advice. Tax treatment (esp. SGB secondary-market redemption and
international-fund holding periods) is contested — confirm with a CA. You are
responsible for SEBI/FYERS algo-trading compliance before arming live.
