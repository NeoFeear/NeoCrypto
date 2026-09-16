# crypto-sim

Simulateur de trading crypto **en papier uniquement** — aucune clé API de trading, aucun ordre
réel, aucun argent réel, à aucun moment. Voir `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md`
pour la spec complète.

## État actuel

Plan 1/6 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py` — termine.
Plan 2/6 (backtest) : filtre de liquidite, 3 strategies, `backtest.py` (telechargement, rejeu, exports CSV) — termine.
Plan 3/6 (moteur live) : persistance SQLite (WAL), strategies pilotables au poll (`step()`), `live_engine.py`
(polling, reprise sur incident, retry/backoff, housekeeping snapshots) — termine.

Lancer le backtest : `python backtest.py`
Lancer le moteur live : `python live_engine.py` (tourne indefiniment, Ctrl+C pour arreter)

Pas encore de notifications Discord, de dashboard ni de deploiement — voir les plans suivants dans
`docs/superpowers/plans/`.

## Développement

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -v
```
