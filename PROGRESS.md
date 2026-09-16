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

## Plan 2/5 — Backtest ✅ terminé (2026-09-16)

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
- [x] Task 7 — Grid : construction des niveaux (1 correctif : bornes non exactes en spacing géométrique pour des ratios non-rationnels)
- [x] Task 8 — Grid : logique de déclenchement (D1-D3) (1 correctif majeur : le code de référence du plan lui-même contredisait son propre test D3 — niveaux adjacents partagent une borne de prix, garde anti-aller-retour-même-bougie ajoutée et documentée)
- [x] Task 9 — `backtest.py` : sélection watchlist + téléchargement (fixture de test corrigée : boucle infinie potentielle dans un provider factice non-stateful)
- [x] Task 10 — `backtest.py` : orchestration du rejeu de stratégies
- [x] Task 11 — `backtest.py` : construction des lignes de rapport
- [x] Task 12 — `backtest.py` : export CSV + table console + `main()` (1 correctif : paramètres de stratégie en dur, déplacés dans `config.yaml`)
- [x] Task 13 — Vérification complète de la suite (93/93)

## Revue finale du Plan 2 (2 tours de correctifs)

La revue globale (après les 13 tâches, toutes déjà approuvées individuellement)
a trouvé 1 problème critique + 5 importants invisibles tâche par tâche :
grid tradait sur une bande de prix (25k-35k) qui ne correspond à aucun
symbole de la watchlist (zéro trade silencieux) ; `ZeroDivisionError` sur
les intervalles 5m/1m ; colonne "vs Buy & Hold" manquante dans le CSV brut ;
incohérence de signe entre `avg_loss` et `biggest_loss` ; `main()` jamais
testé ; un symbole en échec API plantait tout le run. Tous corrigés en 2
tours (95/95 tests, dont un nouveau test bout-en-bout de `main()`).

Note structurelle majeure pour le Plan 3 : les 3 stratégies (pas seulement
grid) gardent leur état dans des variables locales à un seul appel — aucune
n'est appelable "une bougie à la fois" par un moteur live sans redesign.

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

## Plan 3/6 — Moteur live 🚧 en cours (2026-09-16)

Le plan a été scindé en 6 sous-projets après la revue finale du Plan 2 :
le moteur live avait besoin d'un redesign complet de la persistance
d'état des stratégies, pas d'un simple ajout (voir note structurelle
ci-dessus). Plan 3 = moteur live seul ; Discord passe en Plan 4,
dashboard en Plan 5, déploiement en Plan 6.

- [x] Task 1 — Schéma SQLite (`db/schema.sql`) + migration WAL (`db/migrate.py`)
- [x] Task 2 — Repository DB (`db/repository.py`) : trades, lots (resync complet par symbole), snapshots, engine_state
- [x] Task 3 — Refactor Buy & Hold en `BuyHoldState` + `step()` (comportement backtest préservé, régression testée)
- [x] Task 4 — Refactor DCA : ordonnancement par timestamp, supprime le paramètre d'intervalle et son bug de division par zéro
- [x] Task 5 — Grid : état live (`GridState`) + `step_grid_live()` (D2 poll-à-poll) (1 correctif : garde `just_filled` morte supprimée, structurellement inatteignable en mode point-à-point)
- [x] Task 6 — `live_engine.py` : `run_cycle()` + `reconstruct_engine_from_db()` (1 correctif majeur : les compteurs internes `_next_trade_id`/`_next_lot_id` du moteur n'étaient pas réamorcés après un redémarrage — collision d'id silencieuse possible avec les lots pré-existants ; repro écrite et vérifiée par le reviewer avant correctif)
- [x] Task 7 — Retry/backoff sur échec provider (`fetch_with_retry`, 1s puis 4s, 3 tentatives, callback d'échec critique unique, ne lève jamais)

131/131 tests. Suite du plan toujours en cours (Tasks 8-11 restantes :
boucle de polling, purge des snapshots anciens, `main()` CLI, vérification
finale), puis revue finale de branche complète.

## Décisions / rulings notables (Plan 3)

- Erreur opérationnelle repérée et corrigée : le fichier de plan avait
  été édité par erreur dans le repo principal au lieu de la copie du
  worktree pendant la conception des Tasks 6/8/10 — corrigé par
  cherry-pick du commit dans la branche `plan-3-live-engine`.
- Task 6 : `Lot.trade_id_achat` ne peut pas être l'id interne du moteur
  FIFO (compteur privé, pas l'id autoincrement réel de la table
  `trades`) — un `trade_id_map` en mémoire (id moteur → id DB réel),
  possédé par la boucle de polling, fait la traduction avant chaque
  écriture.
- Persistance des lots : resync complet par symbole à chaque cycle
  (`replace_lots_for_symbol` : delete puis reinsert depuis
  `engine.get_lots()`) plutôt qu'un diff incrémental, plus robuste face
  aux SELL et aux cycles multi-BUY.

## À venir

Fin du Plan 3/6 (Tasks 8-11 + revue finale de branche).
Plan 4/6 — Discord (`discord_notifier.py`/`test_notifier.py`, webhooks
réels déjà dans `.env` depuis le 2026-09-16, prêts à être branchés).
Plan 5/6 — Dashboard FastAPI.
Plan 6/6 — `setup.sh` + systemd + déploiement LXC CT303 sur Proxmox.
