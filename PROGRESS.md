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

## Plan 3/6 — Moteur live ✅ terminé (2026-09-16)

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
- [x] Task 8 — Boucle de polling (`run_polling_loop`) avec injection du retry/backoff
- [x] Task 9 — Purge des snapshots (`housekeeping.aggregate_old_snapshots`, fenêtre 30j, agrégation horaire Decimal-exacte) (1 correctif : test ne discriminait pas Decimal de float dans la moyenne)
- [x] Task 10 — `main()` CLI + config `live.active_symbol`/`live.active_strategy`
- [x] Task 11 — Vérification complète de la suite (137/137)

## Revue finale du Plan 3 (4 tours de correctifs, la plus longue chaîne de revue du projet)

La revue globale a trouvé 2 problèmes critiques + 5 importants invisibles
tâche par tâche, tous des angles morts d'intégration entre tâches :

- **Critique : le moteur live ne pouvait recevoir aucune bougie en
  production.** `run_cycle`/`fetch_with_retry` appelaient
  `get_klines(symbol, interval, 0, 0, limit=1)` — vérifié empiriquement
  contre le vrai endpoint Binance (la fenêtre (0,0) renvoie []) et Kraken
  (renvoie [] dès que `end_ms==0`). Chaque test passait uniquement parce
  que les providers factices ignorent start/end. `python live_engine.py`
  aurait tourné indéfiniment sans jamais trader.
- **Critique : un redémarrage remettait `realized_pnl_cumule` à zéro.**
  `reconstruct_engine_from_db` reconstruisait cash + lots mais jamais
  `engine.trades`, dont dépend le calcul du PnL réalisé cumulé — chaque
  redémarrage cassait la continuité de l'historique permanent.
- Important : un cycle n'était pas atomique (5+ commits séparés,
  un crash entre deux pouvait rejouer un signal déjà traité).
- Important : `fetch_with_retry` ne rattrapait que `httpx.HTTPError` ;
  toute autre exception (ex. Kraken) plantait tout le process.
- Important : `housekeeping.aggregate_old_snapshots` (Task 9) était du
  code mort — personne ne l'appelait.
- Important (différé, voir ruling ci-dessous) : la persistance des lots
  est indexée par symbole seul, pas (symbole, stratégie) — bug latent de
  perte de données dès qu'une deuxième stratégie tradera le même symbole.

Tous corrigés en 4 tours de correctifs + re-revues, chacune utilisant du
**test de mutation** (annuler le correctif dans une copie miroir,
confirmer que son propre test échoue) plutôt que de faire confiance aux
rapports. Deux régressions réelles introduites par les correctifs
eux-mêmes ont été détectées et corrigées de cette façon avant le merge :
le handler d'exception du Tour 1 ne faisait pas de rollback (rejouait un
trade en double), et le rollback du Tour 2 ne réconciliait pas le moteur
en mémoire avec la DB après un cycle échoué (PnL réalisé perdu après un
crash mi-cycle). 137 → 146 tests au fil des 4 tours.

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
- **I6 différé explicitement** (pas oublié) : la persistance des lots
  n'est pas encore indexée par (symbole, stratégie), seulement par
  symbole. Aucun bug actuel (une seule stratégie active à la fois via
  `config.yaml`), mais **précondition à vérifier avant d'activer deux
  stratégies concurrentes sur le même symbole** — sinon chaque cycle de
  l'une supprimerait les lots ouverts de l'autre.

Fusionné sur `master` en local le 2026-09-16 (146/146). Push vers
`origin` retardé jusqu'à la fin des 6 plans, sur instruction explicite
de Florian.

## Plan 4/6 — Discord ✅ terminé (2026-09-17)

`discord_notifier.py` (webhooks `.env`, `httpx` direct, pas de
`discord.py`) branché sur le moteur live du Plan 3 : confirmations de
transaction colorées, résumé quotidien 8h Europe/Paris (POST puis PATCH,
jamais dupliqué), alertes seuil (drawdown configurable, échec API,
arrêt anormal), logs démarrage/arrêt. `test_notifier.py` pour validation
visuelle indépendante.

- [x] Task 1 — Config webhooks `.env` + helper HTTP bas niveau (retry 429 unique, ne lève jamais)
- [x] Task 2 — `send_transaction` (gris achat, vert gain, rouge perte)
- [x] Task 3 — `send_alert` + `send_log`
- [x] Task 4 — `send_daily_summary` (POST-ou-PATCH idempotent)
- [x] Task 5 — `test_notifier.py` (script de validation visuelle manuelle)
- [x] Task 6 — `run_cycle` retourne ses trades commités ; `send_transaction` branché dans la boucle (1 défaut de plan repéré et corrigé par l'implémenteur, pas un bug de code : le scénario de test "aucun trade" du plan utilisait buy_hold/cash=0, qui ne produit pas un vrai rejet — substitué par dca)
- [x] Task 7 — Alerte drawdown configurable (`config.yaml`), anti-spam à seuil (2 sites d'appel supplémentaires trouvés et corrigés par l'implémenteur au-delà de la liste du plan)
- [x] Task 8 — Résumé quotidien programmé 8h Europe/Paris (1 correctif d'environnement repéré : `zoneinfo` sans base IANA sur Windows, `tzdata` ajouté en dépendance conditionnelle `win32` uniquement)
- [x] Task 9 — `send_log`/`send_alert` branchés dans `main()` (démarrage, arrêt normal, arrêt anormal)
- [x] Task 10 — Mise à jour README
- [x] Task 11 — Vérification complète de la suite (173/173)

## Revue finale du Plan 4 (1 tour de correctifs)

La revue globale a trouvé 3 problèmes importants, tous des angles morts
d'intégration entre tâches, invisibles tâche par tâche — le même
type de piège que le Plan 3 ("effet de bord avant que son propre état
ne soit durable"), ici sur les notifications plutôt que sur les trades :

- Important : `_post_embed`/`_patch_embed` pouvaient encore lever malgré
  leur contrat "ne plante jamais l'appelant" — 3 chemins d'échappement
  vérifiés empiriquement (`KeyError` sur une réponse 2xx sans champ
  `id`, `JSONDecodeError` sur un corps non-JSON, `httpx.InvalidURL` —
  confirmé n'être PAS une sous-classe de `httpx.HTTPError`).
- Important : le flag "déjà alerté" de l'alerte drawdown n'était pas
  durable avant l'envoi HTTP — si `_check_daily_summary` levait dans le
  même cycle, le rollback de l'appelant effaçait le flag alors que
  l'alerte était déjà envoyée, causant des ré-alertes en boucle (3
  alertes dupliquées reproduites empiriquement sur 3 cycles).
- Important : le résumé quotidien pouvait être posté deux fois pour le
  même jour, même défaut — un crash entre l'envoi réussi et le commit
  différé de l'id du message laissait un message orphelin, provoquant
  un second POST au lieu d'un PATCH (2 ids distincts reproduits
  empiriquement pour un même jour).
- Différé explicitement à Plan 6 : pas de gestionnaire SIGTERM — un
  `systemctl stop` sous le déploiement systemd prévu ne déclenche ni
  `send_log` d'arrêt ni les blocs `except`/`finally` (comportement par
  défaut de Python sur SIGTERM).

Tous corrigés en 1 tour de correctifs + re-revue, même méthodologie que
le Plan 3 (vérification indépendante des tests de régression contre une
copie du code d'avant correctif, pas seulement confiance dans le
rapport). 173 → 177 tests.

## Décisions / rulings notables (Plan 4)

- Task 7 : au-delà des 9 sites d'appel de `run_polling_loop` listés dans
  le plan, l'implémenteur a trouvé et corrigé main()'s propre appel et
  `tests/test_backtest_main.py`'s `_fake_config()`, tous deux cassés
  silencieusement par le nouveau paramètre requis `discord.alert_
  drawdown_threshold_pct` sur `Config`.
- Résidu accepté (pas de nouveau tour) : `_post_embed`'s parsing de
  `Retry-After` via `float(...)` lèverait sur un format date RFC 7231
  plutôt que numérique — Discord documente exclusivement un format
  numérique en secondes, donc atteignabilité quasi nulle en pratique.
- Même limitation de schéma que le Plan 3 (I6 déjà noté) : les clés
  d'état des notifications (peak drawdown, résumé quotidien) sont
  indexées par (symbole, stratégie), mais `portfolio_snapshots` reste
  indexé par symbole seul — cohérent avec la limitation déjà connue,
  pas un nouveau problème.

Fusionné sur `master` en local le 2026-09-17 (177/177). Push vers
`origin` toujours retardé jusqu'à la fin des 6 plans.

## Plan 5/6 — Dashboard ✅ terminé (2026-09-17)

`dashboard/app.py` : app FastAPI + Jinja2 en lecture seule, 3 pages
server-rendered (Principal, Analyses, Transactions), bandeau simulation
permanent sur chaque page, Chart.js via CDN (aucune dépendance Python
lourde ajoutée). `db/repository.py` gagne `list_trades`/`list_snapshots`/
`list_symbols_with_trades` (seul module de SQL brut, conformément à la
contrainte posée au Plan 3).

- [x] Task 1 — Requêtes lecture seule dans `db/repository.py`
- [x] Task 2 — Squelette FastAPI + Jinja2Templates (chemin absolu dès le
  départ, pas relatif — piège repéré en auto-relecture du plan avant tout
  dispatch)
- [x] Task 3 — Page Principal (`/`) : résumé portefeuille, tableau backtest
- [x] Task 4 — Page Analyses (`/analyses`) : métriques `analytics.py`
- [x] Task 5 — Courbe de valeur du portefeuille (Chart.js)
- [x] Task 6 — Courbe de drawdown + distribution des trades + rendements
  mensuels (formes de retour réelles de `analytics.py` vérifiées par
  l'implémenteur avant intégration — aucun écart avec les hypothèses du plan)
- [x] Task 7 — Page Transactions (`/transactions`) avec filtres type/
  résultat/**période** (filtre période ajouté en auto-relecture du plan,
  absent de la première rédaction — exigence de la spec repérée avant
  dispatch)
- [x] Task 8 — Export CSV filtré (`/transactions/export.csv`)
- [x] Task 9 — Vérification complète de la suite (198/198) + README

## Auto-relecture du plan avant dispatch (5 défauts corrigés avant tout code)

- `Jinja2Templates(directory="dashboard/templates")` (chemin relatif) →
  `Path(__file__).parent / "templates"` (absolu) : un chemin relatif se
  résout contre le CWD du processus au moment du chargement du template,
  ce qui casse sous `monkeypatch.chdir` en test et sous un futur
  `WorkingDirectory=` systemd au Plan 6.
- Test Task 1 avec un conditionnel toujours vrai (code mort) — simplifié.
- Test Task 7 : assertion d'ordre inversée (`list_trades` trie par
  `id DESC`, plus récent d'abord) — corrigée.
- Filtre "période" manquant sur la page Transactions (exigence de spec) —
  ajouté de bout en bout (route, helper `_parse_date_boundary`, template,
  lien d'export, tests de bornes inclusives dédiés).
- Bug chaîne-vide-vs-`None` découvert en ajoutant le filtre période : le
  propre formulaire de filtre de la page Transactions soumet
  `symbol=""` pour "Tous", que `list_trades(conn, "")` traiterait comme
  "aucune ligne" plutôt que "pas de filtre" — normalisé `symbol or None`
  à l'intérieur même de `_filtered_trades`, avec un test reproduisant la
  vraie chaîne de requête `?symbol=&trade_type=&outcome=`.

## Revue finale du Plan 5 (1 tour de correctifs + re-revue ciblée)

Revue globale (exécution empirique des routes, pas seulement lecture
statique) : aucun problème critique. 4 problèmes importants, 11 mineurs.

- Important : `get_conn()` appelait `init_db()` à chaque requête — un
  `db_path` erroné créait silencieusement une base vide, un dashboard
  qui a l'air sain mais n'affiche simplement rien (la pire sorte de bug :
  une réponse qui a l'air correcte mais ne l'est pas). Corrigé : connexion
  SQLite en mode `mode=ro` (lecture seule imposée par SQLite lui-même,
  pas seulement par convention), 503 explicite si la base est absente.
- Important : `/analyses` plantait (`DivisionByZero`) si le capital
  initial est à 0, contrairement à `/` qui dégrade déjà proprement.
  Corrigé par le même garde-fou.
- Important : durcissement CWD incomplet (même limitation que partout
  ailleurs dans le projet) — **différé explicitement au Plan 6** : à
  décider dans la conception de l'unité systemd (`WorkingDirectory=` sur
  la racine du repo, ou vrai mécanisme d'ancrage de chemin pour
  `config.yaml`/`db_path`/`backtest_report.csv`).
- Important : métrique "vs Buy & Hold" de la spec absente en live —
  **disclosure plutôt que code** : même limitation de schéma que le
  graphique de prix déjà accepté (`portfolio_snapshots` ne stocke pas le
  prix de marché par snapshot) ; nécessite un changement de schéma futur,
  pas un contournement inventé au Plan 5.
- Mineurs corrigés : colonne "Date/heure" en epoch-ms brut → format
  lisible ; export CSV traitant un PnL réalisé de 0 (break-even) comme
  vide (`or ""` sur un `Decimal("0")`, falsy en Python) ; import `csv`
  dupliqué ; le symbole sélectionné ne survivait pas à la navigation
  entre pages.
- Mineurs non corrigés (jugés non prioritaires par le relecteur) : deux
  lectures indépendantes par requête, config reparsée 3-4x/requête,
  risque `KeyError` sur `INTERVAL_MS`, et plusieurs points stylistiques.

Un tour de correctifs groupé (sonnet) a introduit une régression propre
mais indépendante : `test_root_page_shows_simulation_banner` était le
seul test du fichier à ne pas simuler `get_conn`, donc il tapait la
vraie route `/` contre la vraie config — le passage à `mode=ro` le
transformait en 503 sur un clone propre, masqué en local par un
`crypto_sim.db` orphelin (ignoré par git) déjà présent dans le worktree.
Trouvé par la re-revue ciblée (opus) via un clone `git archive` vraiment
propre, corrigé en une ligne (aligné sur la convention du reste du
fichier), re-vérifié indépendamment via un second clone propre :
203/203, aucun fichier de base présent.

Note administrative : 3 des 4 commits du tour de correctifs n'ont pas le
trailer `Co-Authored-By` (oubli de l'agent, seul le 4ème l'a). Une
réécriture d'historique pour l'ajouter a été bloquée par le classificateur
de sécurité du bac à sable ("action git destructive"), aussi bien pour
l'agent que pour moi. Sans conséquence sur le contenu (rien n'est perdu,
commits locaux jamais poussés) — signalé à Florian, pas corrigé.

Fusionné sur `master` en local le 2026-09-17 (198 → 203 tests). Push vers
`origin` toujours retardé jusqu'à la fin des 6 plans.

## Plan 6/6 — Déploiement ✅ terminé (2026-09-17)

`live_engine.py` gagne un gestionnaire SIGTERM (différé du Plan 4) qui
route proprement vers le même chemin d'arrêt que Ctrl+C. `deploy/` : 2
unités systemd durcies (moteur live + dashboard), `setup.sh` (provisioning
applicatif idempotent), `provision-ct303.sh` (création LXC côté Proxmox).
CT303 **réellement provisionné et déployé** sur le Proxmox (192.168.1.54,
CT303 = 192.168.1.84, Debian 13, non privilégié, DHCP, timezone
Europe/Paris) — pas seulement écrit, exécuté pour de vrai.

- [x] Task 1 — Gestionnaire SIGTERM (`_raise_keyboard_interrupt`/
  `install_signal_handlers`), un seul chemin d'arrêt pour Ctrl+C et SIGTERM
- [x] Task 2 — 2 unités systemd durcies (NoNewPrivileges, ProtectSystem=strict,
  ProtectHome, PrivateTmp, utilisateur dédié `cryptosim`)
- [x] Task 3 — `deploy/setup.sh` (1 tour de correctifs : BOM UTF-8 accidentel
  dans le script + test de syntaxe bash cassé sur Windows car `bash` résolvait
  vers le stub WSL de System32 plutôt que le vrai bash de Git Bash — les deux
  corrigés et re-vérifiés)
- [x] Task 4 — `deploy/provision-ct303.sh` (1 réclamation de relecteur écartée :
  prétendait qu'un fichier test n'existait pas alors qu'il existait bel et
  bien — vérifié directement, classée sans suite)
- [x] Task 5 — Mise à jour README (runbook de déploiement complet)

## Revue finale du Plan 6 (1 tour de correctifs + re-revue, opus)

1 Critical, 6 Important, 10 Minor. Le Critical retombait directement sur
un défaut du plan que j'ai écrit moi-même : l'unité systemd du dashboard
avait `ProtectSystem=strict` sans aucun `ReadWritePaths`, en pensant que
"le dashboard n'écrit jamais donc c'est une couche de durcissement
gratuite" — sans tenir compte du fait que SQLite en mode WAL a besoin
d'ouvrir le fichier `-shm` en lecture-écriture pour qu'un lecteur puisse
prendre un verrou de lecture. Résultat réel : le dashboard aurait affiché
`active` côté systemd tout en servant du 503 sur chaque page dès que le
moteur live écrit activement, avec un message d'erreur trompeur ("base de
données introuvable"). Corrigé (ReadWritePaths ajouté, test qui verrouillait
la config cassée réécrit), **et vérifié empiriquement en conditions réelles**
lors du déploiement (voir plus bas).

Autres corrections (Important) : dépendance à `sudo` non garantie dans le
LXC → `runuser` ; chemin DB en dur dans `setup.sh` → lu depuis
`config.yaml` ; aucun test ne vérifiait `WorkingDirectory=/opt/crypto-sim`
(la directive la plus structurante du plan) ; aucun garde-fou BOM/CRLF
malgré la régression déjà survenue en Task 3 ; `provision-ct303.sh`
traitait un CT303 arrêté comme "rien à faire" au lieu de le redémarrer ;
le `tar` du README n'excluait pas `.env`, ce qui aurait fait fuiter les 4
webhooks Discord dans une archive lisible par tous sans nettoyage.
Corrections mineures groupées : avertissement de redémarrage manquant
dans `setup.sh`, `PYTHONUNBUFFERED`/`SyslogIdentifier` sur les 2 unités,
scripts non exécutables, `DEBIAN_FRONTEND=noninteractive`, timezone
manquante sur `provision-ct303.sh`. 221 → 235 tests, tous vérifiés
indépendamment par la re-revue (blobs pré-correctif re-dérivés, chaque
assertion confirmée rouge-puis-verte).

Différé/documenté sans changement de code : la portée de
`ReadWritePaths=/opt/crypto-sim` couvre le répertoire de code, pas
seulement les données (la spec §14 demandait plus étroit — même
catégorie de déviation assumée que la décision logrotate/CT104 du Plan 5,
un `StateDirectory=` séparé casserait le workflow dev Windows) ; 2
fenêtres de micro-race SIGTERM déjà documentées en commentaire dans
`live_engine.py` (le rapporteur lui-même a dit "à documenter, pas à
corriger").

## Déploiement réel (Task 6, exécuté directement par la session orchestratrice)

- Vérifié avant exécution : noms de storage (`local`, `local-lvm`) et
  bridge (`vmbr0`) conformes aux hypothèses du script ; version du
  template `debian-13-standard` corrigée de `13.1-1` (obsolète) à `13.6-1`
  (réellement en cache) avant de lancer `provision-ct303.sh` pour de vrai.
- `provision-ct303.sh` exécuté sur l'hôte Proxmox : CT303 créé et démarré,
  IP DHCP **192.168.1.84**, timezone Europe/Paris.
- Code transféré via tar (exclusion vérifiée de `.env`/`*.db`) + `pct push`
  + extraction (avec `--no-same-owner`, le tar Windows enregistrait des
  UID/GID que le conteneur non privilégié refusait de restaurer) ; `.env`
  transféré séparément, jamais dans l'archive ; toutes les copies
  temporaires nettoyées sur les 3 hôtes (local, Proxmox, CT303) après coup.
- `setup.sh` exécuté dans CT303 : réussi du premier coup après les
  corrections de la revue finale.
- Vérification empirique complète : les 2 services `active`, le moteur
  live a réellement tourné un cycle (bougie Binance récupérée, 1 trade
  DCA exécuté, 3 notifications Discord envoyées avec succès), 1 ligne
  dans `portfolio_snapshots` et `trades` (pas juste "démarré sans
  planter"), dashboard `http://192.168.1.84:8303/` → 200 avec bannière
  simulation et symbole BTCUSDT réellement affiché.
- **Test du gestionnaire SIGTERM en conditions réelles** :
  `systemctl stop crypto-sim.service` → log "Arret demande.", notification
  Discord d'arrêt envoyée, `Result=success`/`ExecMainStatus=0`, arrêt
  propre sans dépassement du `TimeoutStopSec` ni SIGKILL. Redémarré
  ensuite (`systemctl start`), dashboard re-testé à 200 après coup —
  **cette transition stop/start exerce exactement le scénario WAL/`-shm`
  du correctif Critical, confirmant empiriquement qu'il tient.**
- Entrée Homepage (CT104) : **non faite**, décision délibérée — CT104 est
  volontairement arrêté depuis une session précédente ("homepage ne me
  sert pas"), pas un oubli.

Note administrative (comme au Plan 5) : les commits du tour de correctifs
final n'ont pas systématiquement le trailer `Co-Authored-By` sur chaque
commit — signalé, sans conséquence sur le contenu, commits locaux avant
fusion.

Fusionné sur `master` en local le 2026-09-17 (203 → 235 tests, +1 commit
correctif de version de template pendant l'exécution de Task 6).

**Les 6 plans sont maintenant terminés.** Prochaine étape : premier push
vers `origin` (GitHub `NeoFeear/NeoCrypto`), conformément à l'instruction
explicite de Florian ("push et commit quand t'auras tout fini"), confirmée
via question directe pendant cette session.

## 2026-09-25 — Revue externe « Revue NeoCrypto » appliquee + incidents live

- **Depot = production** : import du code et de la config qui tournaient sur CT303
  (commit `sync:`), puis deploiement uniquement via `deploy/deploy-to-ct303.sh` (HEAD,
  sauvegarde, tests dans le conteneur, redemarrage seulement si vert).
- **CRITIQUE (revue) — grille appariee au lot de son palier** : `FifoEngine.sell(...,
  lot_price=)` vend le lot achete par le palier ; le cas 33 000 -> 34 000 de la revue
  enregistre +2,93 $ au lieu de -0,0139 $. DCA inchange (FIFO). Repli FIFO pour les
  paliers remplis avant le correctif.
- **MODERE (revue) — live = backtest** : `fill_model: range` (plus bas/plus haut de la
  bougie 5 min), garde-fous : pas d'aller-retour intra-bougie, pas d'execution sur la
  bougie de construction de la grille.
- **Capital de reference** : rendements mesures contre le cash de depart reel de chaque
  paire (`starting_capital`) ; les rapports affichaient +16,5 % pour un reel de +4,8 %.
- **Incident Discord** : un 429 a `Retry-After: 1992` endormait un thread de paire 33 min ;
  envoi en arriere-plan (file, cadence par webhook, reessais reseau), un seul message
  demarrage/arret, plafond d'attente en ligne de 15 s.
- **Securite** : les URL de webhooks ne sont plus ecrites dans journald (logs httpx) ;
  authentification HTTP Basic optionnelle du dashboard (`DASHBOARD_USER`/`PASSWORD`).
- Dashboard : chiffres a 2 decimales. Journaux : rejets identiques masques 6 h.
- 309 tests (235 au Plan 6 -> 289 a l'import de CT303 -> 309).
