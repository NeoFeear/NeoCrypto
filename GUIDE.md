# Guide d'accès et d'utilisation — crypto-sim

⚠ **Rappel** : ceci est un simulateur de trading **en papier uniquement**. Aucune clé API de
trading, aucun ordre réel, aucun argent réel, à aucun moment. Tout ce que fait ce projet peut
être arrêté, modifié ou effacé sans aucune conséquence financière.

## En bref

- **Code source** : https://github.com/NeoFeear/NeoCrypto (branche `master`)
- **Tourne sur** : CT303 sur ton Proxmox (192.168.1.54), IP `192.168.1.84`, Debian 13
- **Dashboard (lecture seule)** : http://192.168.1.84:8303/ — accessible depuis n'importe quel
  appareil sur ton LAN, aucune authentification (usage interne uniquement)
- **Discord** : 4 salons déjà configurés (transactions, résumé quotidien, alertes, logs
  démarrage/arrêt) — si tu as suivi la checklist Discord, ils reçoivent déjà les notifications

## 1. Voir ce que fait le simulateur (le plus simple)

Ouvre http://192.168.1.84:8303/ dans un navigateur sur ton réseau local. Trois pages :

- **Principal** (`/`) : valeur du portefeuille, PnL réalisé/latent, rendement %, graphique
  valeur + résultats du dernier backtest comparatif
- **Analyses** (`/analyses`) : Sharpe, Sortino, Calmar, profit factor, expectancy, courbe de
  drawdown, distribution des trades, rendements mensuels
- **Transactions** (`/transactions`) : historique complet des trades, filtrable par type/
  résultat/période, export CSV

Le sélecteur de symbole en haut de page change les données affichées (BTCUSDT, ETHUSDT, etc.
selon ta `watchlist` dans `config.yaml`).

## 2. Se connecter à CT303 (pour aller plus loin)

Depuis ta machine Windows (le repo local est dans `E:\Sites\crypto-sim`) :

```bash
ssh -i C:\Users\dark5\.ssh\id_ed25519_proxmox root@192.168.1.54
pct enter 303
```

Ou directement une commande sans ouvrir de shell interactif :

```bash
ssh -i C:\Users\dark5\.ssh\id_ed25519_proxmox root@192.168.1.54 "pct exec 303 -- <commande>"
```

## 3. Vérifier que tout tourne bien

```bash
pct exec 303 -- systemctl status crypto-sim.service crypto-sim-dashboard.service
```

Voir les logs en direct (Ctrl+C pour sortir) :

```bash
pct exec 303 -- journalctl -u crypto-sim.service -f
pct exec 303 -- journalctl -u crypto-sim-dashboard.service -f
```

## 4. Arrêter / relancer

```bash
pct exec 303 -- systemctl stop crypto-sim.service      # arrêt propre (notif Discord envoyée)
pct exec 303 -- systemctl start crypto-sim.service
pct exec 303 -- systemctl restart crypto-sim.service    # après une modif de config.yaml
```

Les deux services redémarrent automatiquement en cas de crash (`Restart=on-failure`) et au
redémarrage de CT303 (`WantedBy=multi-user.target`). Un `systemctl stop` volontaire, lui, ne
redémarre pas tout seul — c'est voulu.

Pour tout arrêter complètement (le moteur ET le dashboard) :

```bash
pct exec 303 -- systemctl stop crypto-sim.service crypto-sim-dashboard.service
```

## 5. Changer la configuration

Le fichier de config vit dans le conteneur à `/opt/crypto-sim/config.yaml`. Tu peux l'éditer
directement :

```bash
pct exec 303 -- nano /opt/crypto-sim/config.yaml
```

Ce que tu peux changer sans risque (relance requise après, voir section 4) :

- `live.active_symbol` : le symbole tradé en live (doit être dans `watchlist`)
- `live.active_strategy` : `buy_hold`, `dca`, ou `grid`
- `strategy_defaults.<strategie>.*` : paramètres de chaque stratégie (montant DCA, bornes de
  la grille, etc.)
- `discord.alert_drawdown_threshold_pct` : seuil d'alerte drawdown
- `liquidity.*`, `fees.*`, `backtest.*` : filtres et paramètres du backtest
- `dashboard.port` : port du dashboard (8303 par défaut)

Après toute modification : `systemctl restart crypto-sim.service` (et
`crypto-sim-dashboard.service` si tu as touché `dashboard.port`).

## 6. Relancer un backtest

Pour comparer les 3 stratégies sur la `watchlist` actuelle avec les paramètres actuels :

```bash
pct exec 303 -- bash -c "cd /opt/crypto-sim && .venv/bin/python backtest.py"
```

Ça régénère `backtest_report.csv` et `analytics_report.csv` dans `/opt/crypto-sim`, que la page
Principal du dashboard affiche automatiquement.

## 7. Mettre à jour le code après un changement sur ta machine

Tant que tu modifies le code depuis `E:\Sites\crypto-sim`, le plus simple maintenant que le
repo est sur GitHub :

```bash
# Sur CT303, une seule fois pour connecter le dossier au dépôt distant :
pct exec 303 -- bash -c "cd /opt/crypto-sim && git init && git remote add origin https://github.com/NeoFeear/NeoCrypto.git && git fetch && git checkout -f master"

# Ensuite, à chaque mise à jour :
pct exec 303 -- bash -c "cd /opt/crypto-sim && git pull"
pct exec 303 -- systemctl restart crypto-sim.service crypto-sim-dashboard.service
```

(`crypto_sim.db` et `.env` sont dans `.gitignore`, donc un `git pull`/`checkout` ne touche
jamais ni à tes données ni à tes secrets.)

## 8. Où sont les données et les secrets

- Base de données (trades, snapshots) : `/opt/crypto-sim/crypto_sim.db` (SQLite) — jamais dans
  le dépôt Git
- Webhooks Discord : `/opt/crypto-sim/.env` — jamais dans le dépôt Git, permissions restreintes
  (600, lisible seulement par l'utilisateur système `cryptosim`)

## 9. Documentation technique complète

- `docs/superpowers/specs/2026-09-15-crypto-paper-trading-sim-design.md` — la spec complète du
  projet (architecture, décisions de conception)
- `docs/superpowers/plans/` — les 6 plans d'implémentation détaillés (un par sous-système)
- `PROGRESS.md` — journal de bord complet : ce qui a été fait, les bugs trouvés en revue et
  corrigés, les décisions prises en cours de route
- `README.md` — démarrage rapide côté développement (venv, tests, lancement local)

## 10. Ce que tu peux faire, en résumé

- Regarder le dashboard n'importe quand, ça ne coûte rien et ne modifie rien
- Changer le symbole ou la stratégie active dans `config.yaml`, relancer le service
- Arrêter/relancer les services librement, aucune perte de données (tout est en base)
- Relancer un backtest pour comparer d'autres paramètres
- Modifier le code, pousser sur GitHub, tirer sur CT303 — le cycle normal de dev

Rien de tout ça ne touche à de l'argent réel ou à un exchange réel : le pire qui puisse arriver
est de perdre l'historique de trades simulés (qui se régénère au prochain cycle de toute façon).
