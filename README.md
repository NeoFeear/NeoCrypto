# crypto-sim

Simulateur de trading crypto **en papier uniquement** — aucune clé API de trading, aucun ordre
réel, aucun argent réel, à aucun moment. Voir `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md`
pour la spec complète.

## État actuel

Plan 1/6 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py` — termine.
Plan 2/6 (backtest) : filtre de liquidite, 3 strategies, `backtest.py` (telechargement, rejeu, exports CSV) — termine.
Plan 3/6 (moteur live) : persistance SQLite (WAL), strategies pilotables au poll (`step()`), `live_engine.py`
(polling, reprise sur incident, retry/backoff, housekeeping snapshots) — termine.
Plan 4/6 (Discord) : `discord_notifier.py` (transactions, resume quotidien, alertes seuil, logs demarrage/arret) — termine.
Plan 5/6 (dashboard) : FastAPI + Jinja2, pages Principal/Analyses/Transactions, bandeau simulation permanent — termine.

Lancer le backtest : `python backtest.py`
Lancer le moteur live : `python live_engine.py` (tourne indefiniment, Ctrl+C pour arreter)
Valider les notifications Discord avant le premier lancement du moteur live : `python test_notifier.py`
(necessite `.env` rempli avec les 4 webhooks -- voir `.env.example`).
Lancer le dashboard : `python -m dashboard.app` (port configurable dans `config.yaml`, defaut 8303).

Pas encore de deploiement — voir `docs/superpowers/plans/` pour le plan suivant (Plan 6/6).

## Développement

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -v
```
