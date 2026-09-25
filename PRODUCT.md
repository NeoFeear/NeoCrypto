# Product

<!-- impeccable:product-schema 1 -->

> Inferred on 2026-09-26 from the spec (`docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md`),
> the live CT303 data and the user's standing brief ("améliore ce que tu veux… épate moi"). No interview:
> the user delegated and was asleep. Items marked *(inferred)* are hypotheses.

## Platform

web

## Users

- **Florian**, the only user: a developer learning how trading strategies behave, on paper only.
  Checks the dashboard on the LAN from a desktop and, *(inferred)*, from his phone; Discord
  carries the push side (trades, daily summary, alerts).

## Product Purpose

A paper-trading laboratory: no API keys, no real orders, no real money, ever. It runs DCA,
grid and buy & hold on 9 live Binance pairs (5-minute polling) and in 90-day backtests, and
judges them with quant metrics (Sharpe, Sortino, Calmar, drawdown, profit factor, expectancy,
alpha vs buy & hold) rather than raw return. Success = understanding *why* a strategy does
what it does, on honest numbers.

## Positioning

An experiment bench, not a brokerage: several strategies run side by side on the same market
with the same rules, every number reproducible from the SQLite record, the simulation status
impossible to forget.

## Operating Context

- 9 live pairs, each with its own real starting cash (1111.11 engaged in total as of 2026-09-26),
  strategy per pair (dca / grid), trades a few per day.
- Pages: Principal (per pair: value, realized/latent PnL, return, value curve, backtest table),
  Analyses (risk metrics, drawdown curve, trade distribution, monthly returns), Transactions
  (filters, totals, CSV export).
- The permanent banner "⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé" is a
  product rule and is tested.

## Capabilities and Constraints

- FastAPI + Jinja2, Chart.js from jsDelivr, no build step; read-only SQLite (WAL) access.
- All money in USDT (quote asset); never relabel to EUR.
- Snapshots store portfolio value, not market price.
- 309 tests; dashboard tests assert specific strings and chart JSON.

## Brand Commitments

- Name "crypto-sim" (repo NeoCrypto). French UI. The simulation banner text is fixed.

## Product Principles

1. Never let it look like real money: simulation is part of the identity, not a footnote.
2. Compare, don't just report: strategies and pairs side by side, against where they started.
3. Risk before return: drawdown and consistency are as visible as gains.
4. Every number traceable to the record; missing history said plainly ("N/A"), never faked.
