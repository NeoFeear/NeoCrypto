# crypto-sim

Simulateur de trading crypto **en papier uniquement** — aucune clé API de trading, aucun ordre
réel, aucun argent réel, à aucun moment. Voir `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md`
pour la spec complète.

## État actuel

Plan 1/5 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py` — terminé.
Plan 2/5 (backtest) : filtre de liquidité, 3 stratégies (Buy & Hold, DCA, Grid), `backtest.py` (téléchargement,
rejeu, exports CSV `backtest_report.csv` + `analytics_report.csv`) — terminé.

Lancer le backtest : `python backtest.py` (nécessite `pip install -r requirements.txt`).

Pas encore de moteur live, de dashboard ni de déploiement — voir les plans suivants dans
`docs/superpowers/plans/`.

## Développement

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -v
```
