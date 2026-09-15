# Progress — crypto-sim

⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé. Voir
`docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md`
pour la spec complète, et `docs/superpowers/plans/` pour le plan
d'implémentation détaillé de chaque étape (tâches, code, tests).

Mis à jour au fil de l'eau à mesure que les tâches sont terminées et
revues (implémentation + revue par un second agent, systématique).

## Plan 1/5 — Fondation ✅ terminé, mergé sur `master` (2026-09-15)

`MarketDataProvider` (Binance par défaut, repli Kraken documenté),
moteur FIFO Decimal-exact, `analytics.py` (Sharpe/Sortino/Calmar/
drawdown/profit factor/expectancy/exposure/alpha/distribution/rendements
mensuels). 50 tests, revue finale complète (10 tâches + revue globale +
1 vague de correctifs), tous les rendus verifiés a la main.

## Plan 2/5 — Backtest 🚧 en cours

Filtre de liquidité, 3 stratégies (Buy & Hold, DCA, Grid trading),
`backtest.py` (téléchargement, filtre, rejeu, exports CSV). Ferme aussi
les 5 manques laissés en suspens par la revue finale du Plan 1 (factory
provider, valorisation moteur, `trade_stats`, couverture pagination).

- [x] Task 1 — Provider factory (`market_data/factory.py`)
- [x] Task 2 — Valorisation moteur FIFO (`unrealized_pnl`, `realized_pnl_cumule`)
- [x] Task 3 — `analytics.trade_stats()` + refactor `expectancy()` (1 correctif : déduplication du prédicat sell sur 3 fonctions)
- [x] Task 4 — Filtre de liquidité (`liquidity.py`) (1 correctif : limite exacte du spread non testée)
- [x] Task 5 — Stratégie Buy & Hold + helper `build_snapshot` partagé
- [x] Task 6 — Stratégie DCA (1 correctif : ZeroDivisionError si fréquence < intervalle de bougie)
- [ ] Task 7 — Grid : construction des niveaux
- [ ] Task 8 — Grid : logique de déclenchement (D1-D3)
- [ ] Task 9 — `backtest.py` : sélection watchlist + téléchargement
- [ ] Task 10 — `backtest.py` : orchestration du rejeu de stratégies
- [ ] Task 11 — `backtest.py` : construction des lignes de rapport
- [ ] Task 12 — `backtest.py` : export CSV + table console + `main()`
- [ ] Task 13 — Vérification complète de la suite

## Décisions / rulings notables (Plan 2)

- Task 3 : le prédicat `side==SELL and realized_pnl is not None` était
  dupliqué dans 3 fonctions d'`analytics.py` — extrait dans un helper
  privé `_sell_realized_pnls()` après une revue.
- Task 9 (à venir) : ajout d'une vérification de couverture dans
  `download_backtest_klines` (WARNING si < 95% des bougies attendues) —
  ferme le manque de pagination laissé en suspens par la revue finale
  du Plan 1.
- Task 4 : la limite exacte du spread (`spread_bps == max_spread_bps`)
  n'était pas testée (seulement un cas largement au-dessus) — 1
  correctif pour ajouter le test à la limite exacte.

## À venir

Plan 3/5 — Moteur live + Discord (`discord_notifier.py`/`test_notifier.py`
codés et testés avec webhooks mockés ; les vraies notifications restent
coupées jusqu'à ce que Florian remplisse `.env` avec les webhooks
réels — déjà fait le 2026-09-16).
Plan 4/5 — Dashboard FastAPI.
Plan 5/5 — `setup.sh` + systemd + déploiement LXC CT303 sur Proxmox.
