# crypto-sim

Simulateur de trading crypto **en papier uniquement** — aucune clé API de trading, aucun ordre
réel, aucun argent réel, à aucun moment. Voir `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md`
pour la spec complète.

## État actuel

Plan 1/5 (fondation) : `MarketDataProvider` (Binance + repli Kraken), moteur FIFO, `analytics.py`.
Pas encore de base de données, de backtest, de moteur live, de dashboard ni de déploiement —
voir les plans suivants dans `docs/superpowers/plans/`.

## Développement

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -v
```
