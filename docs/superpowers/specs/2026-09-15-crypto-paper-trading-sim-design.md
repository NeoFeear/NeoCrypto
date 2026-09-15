# Simulateur de trading crypto en papier — design

Statut : validé par Florian (2026-09-15) — voir décisions de clarification en fin de document.
Repo local : `E:\Sites\crypto-sim`, pas de remote git (convention identique à `noctavira-social`).
Déploiement cible : LXC CT303 sur Proxmox (192.168.1.54), Debian 13, provisionné à l'étape 8 seulement.

## 1. Objectif

Simulateur de trading crypto **en papier uniquement** : aucune clé API de trading, aucun ordre réel,
aucun argent réel, à aucun moment. Usage pédagogique/exploratoire — comparer des stratégies simples
sur des données de marché réelles, avec un niveau d'analyse comparable à ce qu'un trader quant
utiliserait pour juger une stratégie (pas juste "rendement %").

## 2. Contraintes non négociables

- Zéro clé API avec permission de trading, zéro ordre réel, à aucun moment.
- Tous les champs prix/quantité/PnL en `Decimal` (module Python `decimal`), jamais en `float`.
- Un `BUY` est rejeté (loggé, jamais exécuté) si `cash_balance` insuffisant — pas de cash négatif,
  pas de levier implicite.
- Un `SELL` dont la quantité demandée dépasse la quantité totale disponible dans les lots FIFO est
  **rejeté en totalité**, loggé en WARNING, aucun fill partiel (symétrique à la règle BUY — voir
  décision D4).
- Seuils (liquidité, spread, rétention snapshots, montants, frais) dans un fichier de config
  (`config.yaml`), jamais en dur dans le code.
- URLs de webhook Discord dans `.env`, fournies après coup par Florian, jamais committées
  (`.gitignore`), jamais demandées en conversation.
- La source de données de marché est interchangeable (`MarketDataProvider`) — le reste du pipeline
  (FIFO, backtest, live, dashboard) n'appelle jamais un exchange directement.

## 3. Architecture

```
market_data/
  provider.py        # interface MarketDataProvider (klines, book_ticker, ticker_24h)
  binance.py          # implémentation par défaut — data-api.binance.vision, sans clé
  kraken.py            # repli documenté — api.kraken.com/0/public, sans clé
engine/
  fifo_engine.py       # moteur FIFO : BUY/SELL, lots, cash_balance, rejets
  strategies/
    dca.py
    grid.py
    buy_hold.py
analytics.py            # métriques trader (Sharpe, Sortino, Calmar, etc.) — fonctions pures
backtest.py              # téléchargement paginé, filtres, rejeu 3 stratégies, exports CSV
live_engine.py            # polling 5 min, reprise sur incident, retry/backoff, snapshots
discord_notifier.py        # embeds via webhooks, anti-spam
dashboard/
  main.py                  # FastAPI
  templates/                # Jinja
db/
  schema.sql
  migrate.py
config.yaml
.env.example
test_notifier.py
setup.sh
crypto-sim.service
```

Aucune donnée de marché n'est jamais lue directement depuis `binance.py`/`kraken.py` en dehors de
`market_data/` — le backtest, le moteur live et le dashboard ne connaissent que
`MarketDataProvider`.

## 4. Source de données (section 1 du brief)

Contexte réglementaire (France/MiCA, depuis le 2026-07-01) : Binance n'a plus l'agrément CASP pour
le trading avec des résidents français (ouverture de compte, dépôt EUR, spot suspendus ; retraits
seuls disponibles). Ce projet **n'ouvre aucun compte et n'envoie aucun ordre** — il utilise
uniquement `data-api.binance.vision` (klines, bookTicker, ticker/24hr), en lecture publique sans
authentification. Ce n'est pas le service suspendu, mais la situation peut évoluer — d'où
l'abstraction et le repli Kraken documentés dès le départ.

```python
class MarketDataProvider(Protocol):
    def get_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Kline]: ...
    def get_book_ticker(self, symbol: str) -> BookTicker: ...   # bid/ask pour le spread
    def get_ticker_24h(self, symbol: str) -> Ticker24h: ...      # volume quote 24h
```

- `binance` (défaut) : `GET /api/v3/klines`, `GET /api/v3/ticker/bookTicker`,
  `GET /api/v3/ticker/24hr` sur `data-api.binance.vision`.
- `kraken` (repli, agrément CASP valide en France) : `GET /0/public/OHLC`, `GET /0/public/Ticker`
  sur `api.kraken.com`. Mappé vers le même format `Kline`/`BookTicker`/`Ticker24h`.
- Sélection via `DATA_SOURCE: binance | kraken` dans `config.yaml`.

Pagination obligatoire pour l'historique (90 jours en 1h ≈ 2160 bougies, > limite 1000/requête) —
boucle sur `startTime`/`endTime`, `time.sleep(0.2)` entre appels (rate limit).
Live : polling 5 min, une seule bougie récente (pas de pagination).

## 5. Watchlist et sélection du symbole (section 2)

```python
WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
```

Filtres de liquidité avant inclusion au backtest (seuils configurables dans `config.yaml`) :

- Volume 24h (quote) > 50 000 000 USDT (`ticker/24hr`)
- Spread relatif < 10 bps (0,10 %) : `(ask - bid) / ((ask + bid) / 2)` (`bookTicker`)
- Une paire qui échoue un filtre est exclue et loggée avec la raison.

Le backtest tourne sur chaque paire filtrée × chaque stratégie → tableau comparatif. Le symbole
retenu pour le live est stocké dans `strategy_configs.params` (modifiable via dashboard ou update
en base, sans redéploiement). Le choix final reste une décision manuelle de Florian après lecture
du backtest.

## 6. Modèle de données (SQLite, WAL)

`PRAGMA journal_mode = WAL;` à l'initialisation.

Tous les champs prix/quantité/PnL en `TEXT` (représentation `Decimal` exacte) — jamais en `REAL`.

**trades** : `id, timestamp, symbol, side (BUY/SELL), price, quantity, fee_pct, fee_amount,
total_cost, realized_pnl (NULL si BUY), cash_balance_after, strategy_name`

**lots** (FIFO) : `id, trade_id_achat, symbol, quantity_restante, prix_achat, timestamp_achat`

**portfolio_snapshots** : `id, timestamp, symbol, cash_balance, position_value, total_value,
unrealized_pnl, realized_pnl_cumule`

**strategy_configs** : `id, name, type (dca/grid/buy_hold), symbol, params (JSON), capital_initial,
actif (bool), date_creation`

**engine_state** (reprise sur incident) : `key TEXT PK, value TEXT` — persiste le dernier timestamp
de kline traité par symbole/stratégie, et le `message_id` du résumé Discord du jour.

### Schéma JSON des params par type de stratégie

DCA :
```json
{ "amount_per_buy": 50, "frequency_hours": 24, "reference_price": "close" }
```
`reference_price: "close"` = close de la dernière bougie complète (backtest : bougie au timestamp
du cycle ; live : close de la dernière bougie 1h/5m polling la plus récente disponible).

Grid trading :
```json
{
  "lower_bound": 25000, "upper_bound": 35000, "n_levels": 10,
  "spacing": "arithmetic", "order_size_quote": 100
}
```
`spacing`: `arithmetic` ou `geometric` (géométrique recommandé — couverture proportionnelle en %).

Buy & Hold :
```json
{ "invest_at": "start" }
```

Frais (`fee_pct`) : valeur simulée dans `config.yaml` (`default_fee_pct`, défaut `0.001` = 0,1 %,
frais spot taker Binance typiques), appliquée à chaque trade — pas de champ par stratégie.

## 7. Moteur FIFO (section 3)

- Un `BUY` crée une entrée dans `lots`. Avant tout `BUY` : `cash_balance >= total_cost`, sinon
  rejet loggé en WARNING (aucune position négative, aucun levier implicite — critique pour le grid
  trading, voir décision D3).
- Un `SELL` consomme les lots du même `symbol` par ancienneté (FIFO, consommation partielle possible
  sur plusieurs lots) : `realized_pnl = Σ((prix_vente - prix_achat_lot) × qty_consommée) - frais`,
  puis met à jour/supprime les lots consommés.
- `SELL` dont la quantité demandée dépasse le total disponible : rejet total, WARNING, aucun fill
  partiel (décision D4).

Tests unitaires obligatoires (`fifo_engine.py` livré avec sa suite avant l'étape suivante) :
achat unique puis vente partielle ; vente traversant plusieurs lots ; ventes multiples sur lots
d'âges différents ; vente sans lot disponible (rejet total) ; achat avec cash insuffisant (rejet).

## 8. Stratégies — comportement précisé (clarifications 2026-09-15)

**Grid trading — ré-armement (décision D1)** : chaque niveau peut re-déclencher un `BUY` puis un
`SELL` à chaque fois que le prix le retraverse, pendant toute la durée du run (backtest ou live).
Ce n'est pas un tir unique par niveau — c'est le comportement d'un vrai bot de grid.

**Grid trading — détection de franchissement (décision D2)** :
- Backtest (bougies 1h) : un niveau est considéré franchi si `low <= niveau <= high` sur la bougie
  courante (utilise l'OHLC complet, pas seulement le close — plus réaliste vu la granularité).
- Live (poll 5 min, point instantané) : franchissement détecté par comparaison entre le prix du
  poll précédent et le prix du poll courant (`prev < niveau <= current` ou l'inverse selon le sens).
  Même comportement économique, implémentation adaptée à la donnée disponible dans chaque contexte.

**Grid trading — fills multiples simultanés (décision D3)** : si plusieurs niveaux déclenchent un
`BUY` dans le même cycle et que `cash_balance` ne couvre pas l'ensemble, remplir par ordre de prix
croissant (le moins cher d'abord) jusqu'à épuisement du cash ; chaque niveau non rempli est rejeté
individuellement (WARNING, raison "cash insuffisant").

## 9. Module backtest (section 4)

- Télécharge 90 jours, interval 1h, paginé (section 4), pour chaque paire de la watchlist filtrée.
- Rejoue 3 stratégies (Buy & Hold, DCA, Grid) via le moteur FIFO.
- Capital fictif de départ : 1000 € (configurable dans `config.yaml`).
- Sortie par paire × stratégie : rendement %, nb trades, win rate, gain/perte moyen (gagnant/perdant),
  plus gros gain/perte, max drawdown, frais totaux, comparaison vs Buy & Hold.
- Export `backtest_report.csv` + affichage console.

## 10. analytics.py (section 4bis)

Fonctions pures (trades + snapshots en entrée), testées indépendamment du moteur FIFO :

| Métrique | Définition |
|---|---|
| Rendement total % / CAGR | rendement annualisé |
| Sharpe | rendement moyen / écart-type (annualisé, taux sans risque = 0) |
| Sortino | comme Sharpe, écart-type sur rendements négatifs uniquement |
| Calmar | CAGR / max drawdown |
| Max drawdown % + durée | ampleur + jours de récupération jusqu'au plus haut précédent |
| Profit factor | Σgains / Σ\|pertes\| |
| Expectancy/trade | (win_rate × gain_moyen) − (loss_rate × perte_moyenne) |
| Exposure time % | % du temps investi vs cash |
| Alpha vs Buy & Hold | rendement stratégie − rendement Buy & Hold |
| Distribution des trades | histogramme gains/pertes (buckets), export CSV |
| Rendements mensuels | tableau mois × rendement % |

Export `analytics_report.csv` (paire × stratégie × métriques), réutilisé tel quel par le dashboard.

## 11. Moteur live (section 5, service systemd)

- Polling 5 min sur le symbole actif : à chaque cycle, `get_klines(symbol, interval=live_poll_interval,
  limit=1)` (interval configurable, défaut `"5m"` dans `config.yaml`) — son `close` sert de "prix
  courant" pour la stratégie (référence DCA `reference_price: close`, comparaison prev/current pour
  le franchissement de niveaux grid, décision D2). Applique la stratégie configurée, exécute les
  trades simulés via le moteur FIFO, snapshot le portefeuille à chaque cycle.
- Reprise sur incident : lit `engine_state` avant chaque cycle pour reprendre depuis le dernier
  timestamp traité (jamais rejouer ni sauter un signal).
- Résilience réseau : 1 retry avec backoff exponentiel (1s puis 4s) sur échec d'appel au provider ;
  après 3 échecs consécutifs → `send_alert("api_error", ..., severity="critical")`, passe le cycle
  sans crasher le process.
- État persisté SQLite WAL (survit à un redémarrage du LXC). Logs structurés (INFO trades, DEBUG
  polling).
- Rétention snapshots : détail 5 min sur fenêtre glissante 30 jours ; au-delà, housekeeping
  quotidien agrège en un point horaire (moyenne).

## 12. Dashboard FastAPI (section 6)

- Page principale : valeur totale, PnL réalisé cumulé, PnL latent, rendement % depuis le début, vs
  Buy & Hold ; graphique valeur portefeuille + prix (axe secondaire) ; sélecteur de symbole actif ;
  résultats du dernier backtest comparatif.
- Page "Analyses" (consomme `analytics_report.csv`/`analytics.py`) : Sharpe/Sortino/Calmar/profit
  factor/expectancy/exposure time pour la stratégie live ; courbe de drawdown + durée de
  récupération ; histogramme distribution des trades ; tableau rendements mensuels.
- Page "Transactions" : tableau trié date décroissante (Date/heure, Symbole, Type, Prix, Quantité,
  Montant, Frais, Solde après, Gain/Perte coloré) ; filtres (période, type, gagnant/perdant) ; ligne
  de total ; export CSV filtré.
- Bandeau permanent : `⚠ SIMULATION — Aucun argent réel, aucun ordre réel envoyé`.
- Accès LAN via Homepage (CT104) : pas d'auth applicative. Si exposé au-delà du LAN un jour, auth
  basique au niveau du reverse proxy, pas dans l'app.

## 13. Discord (section 7)

Serveur Discord, catégorie `CRYPTO-SIM`, 4 salons webhook : `#résumé-quotidien`, `#transactions`,
`#alertes`, `#logs-techniques`. `discord_notifier.py` autonome, `httpx` direct sur les webhooks
(embeds) — pas de `discord.py`, pas de bot gateway.

- `send_transaction(...)` → 1 embed par trade **réellement exécuté** (jamais pour un cycle sans
  action, jamais pour un ordre rejeté faute de cash — ça va dans les logs techniques). Vert si gain,
  rouge si perte, gris si achat.
- `send_daily_summary(...)` → 1 seul message/jour (8h Europe/Paris), édité (PATCH) s'il existe déjà,
  jamais dupliqué ; `message_id` lu/écrit dans `engine_state`.
- `send_alert(type, message, severity)` → uniquement seuils réels (drawdown > 10 %, service arrêté,
  3 échecs API consécutifs). Rien si tout va bien.
- `send_log(message, level)` → uniquement démarrage/arrêt du service.
- Aucun heartbeat périodique, aucune notification superflue.
- Erreurs webhook (404, 429) loggées localement, jamais de crash du process appelant. Sur 429 : 1
  retry, délai selon `Retry-After`.
- Config `.env` (4 URLs), jamais committé (`.gitignore`).
- `test_notifier.py` : envoie un exemple de chaque type avec données factices, pour validation
  visuelle indépendante du moteur de trading.

Checklist Discord (à faire une fois, par Florian, avant premier déploiement — pas bloquant pour le
développement du code) : créer serveur/catégorie/4 salons, créer les 4 webhooks, remplir `.env`,
lancer `test_notifier.py` avant de démarrer le moteur live.

## 14. Déploiement (section 8)

- LXC CT303, Debian 13 (`debian-13-standard`), provisionné sur Proxmox (192.168.1.54) **à l'étape
  8 seulement** — pas avant, pour ne pas geler une IP/config avant que le code soit prêt à
  déployer.
- Utilisateur système dédié non-root `cryptosim` ; unité systemd durcie (`NoNewPrivileges=true`,
  `ProtectSystem=strict`, `ReadWritePaths=` limité au répertoire de données).
- `crypto-sim.service` (auto-restart, boot) pour le moteur live.
- Dashboard FastAPI/uvicorn port 8303.
- Rotation logrotate pour tout log fichier additionnel (journald gère déjà systemd).
- Entrée Homepage (CT104) une fois fonctionnel.
- `setup.sh` : venv, `requirements.txt`, migration DB initiale (`PRAGMA journal_mode=WAL`),
  activation services.

## 15. Décisions de clarification (2026-09-15)

| # | Question | Décision |
|---|---|---|
| D1 | Grid : un niveau se déclenche-t-il une fois ou se ré-arme-t-il ? | Ré-armement à chaque traversée, pour toute la durée du run. |
| D2 | Grid : détection de franchissement backtest vs live ? | Backtest : intrabar high/low. Live : comparaison prix précédent/courant entre polls. |
| D3 | Grid : plusieurs niveaux déclenchés, cash insuffisant pour tous ? | Remplissage par prix croissant, rejet individuel loggé du reste. |
| D4 | FIFO : SELL demandant plus que disponible ? | Rejet total, WARNING, aucun fill partiel. |

## 16. Ordre de construction (gates de test obligatoires)

1. Interface `MarketDataProvider` + implémentation Binance (repli Kraken documenté)
2. Moteur FIFO (`fifo_engine.py`) + tests unitaires (section 7 de ce doc)
3. `analytics.py` + tests sur jeu de trades factice
4. Backtest : téléchargement paginé, filtres liquidité/spread, rejeu 3 stratégies, exports CSV
5. Moteur live : polling, reprise sur incident, retry/backoff
6. `discord_notifier.py` + `test_notifier.py`
7. Dashboard FastAPI
8. `setup.sh` + unité systemd + provisionnement CT303 + README

Chaque étape doit avoir ses tests verts, montrés, avant de passer à la suivante.
