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
Plan 6/6 (deploiement) : `deploy/setup.sh` (provisioning applicatif idempotent), 2 unites systemd durcies
(moteur live + dashboard), `deploy/provision-ct303.sh` (creation LXC cote Proxmox) — termine.

## État live (CT303)

Le moteur live tourne actuellement sur 9 paires (`live.pairs` dans `config.yaml` du
conteneur) : `BTCUSDT`/dca, `ETHUSDT`/dca, `BNBUSDT`/grid, `SOLUSDT`/grid, `XRPUSDT`/grid,
`ADAUSDT`/grid, `DOGEUSDT`/grid, `LINKUSDT`/grid, `ZECUSDT`/grid.

2026-09-22 : `BTCUSDT`/`ETHUSDT` (dca) ont ete retires temporairement plus tot dans la
journee (pas en positif), puis reintegres le jour meme a la demande de Florian. Leur etat
(`engine_state`/`lots`, keye par `symbol:strategy`) a repris exactement ou il s'etait
arrete — aucune perte d'historique.

**Source de verite :** depuis le 2026-09-25, ce depot contient exactement ce qui tourne sur
CT303 (import du code et de la config de production, puis corrections de la revue). Regle :
**ne plus jamais modifier `/opt/crypto-sim` directement** -- commiter ici, puis
`bash deploy/deploy-to-ct303.sh` (deploie `HEAD`, garde une sauvegarde, lance les tests
dans le conteneur et ne redemarre que s'ils passent). `DEPLOYED_REVISION` sur CT303 donne le
commit en service.

### Modele d'execution

- **Grille** : chaque palier revend *son propre lot* (meme prix d'achat, meme quantite), plus
  le lot le plus ancien -- taux de reussite, facteur de profit et esperance sont donc justes
  par palier. Le DCA reste en FIFO. Les paliers remplis avant ce changement retombent sur le
  FIFO a leur prochaine vente.
- **Live = backtest** (`strategy_defaults.grid.fill_model: range`) : un palier est execute
  quand le [plus bas, plus haut] de la bougie 5 min cloturee le touche, comme dans le
  backtest ; jamais d'aller-retour dans une meme bougie, jamais sur la bougie qui a servi a
  construire la grille. `cross` = ancien modele cloture a cloture.
- **Ecart restant avec le reel** (volontaire, simulateur papier) : un prix touche est
  considere execute en totalite (pas de file d'attente ni d'execution partielle), frais
  fixes 0,1 %, pas de rejet d'exchange ni de coupure de flux simulee. Les chiffres disent
  comment une strategie se comporte sur le marche, pas ce qu'elle rapporterait en reel.
- **Capital de reference** : le rendement d'une paire est mesure contre le cash avec lequel
  elle a reellement demarre (deduit de son premier trade), meme si `live.pairs` a change de
  taille depuis. Ex. : 8 paires ont demarre a 1000/8 = 125, ZEC a 1000/9 = 111,11 -- capital
  engage total 1111,11.

### Juger une strategie

Un DCA qui achete dans une baisse est en latent negatif *par construction* : sur quelques
jours, son resultat n'est que du bruit de marche. Le juger sur des mois, contre un achat
unique fait au meme moment avec le meme capital (`python backtest.py` donne l'alpha contre
buy & hold), jamais sur la couleur du latent d'une semaine.

### Dashboard

Ecoute sur `0.0.0.0` sans authentification par defaut (LAN uniquement). Avant toute
exposition hors LAN, renseigner `DASHBOARD_USER` et `DASHBOARD_PASSWORD` dans `.env` :
l'authentification HTTP Basic s'active automatiquement.

Lancer le backtest : `python backtest.py`
Lancer le moteur live : `python live_engine.py` (tourne indefiniment, Ctrl+C ou SIGTERM pour arreter proprement)
Valider les notifications Discord avant le premier lancement du moteur live : `python test_notifier.py`
(necessite `.env` rempli avec les 4 webhooks -- voir `.env.example`).
Lancer le dashboard : `python -m dashboard.app` (port configurable dans `config.yaml`, defaut 8303).

## Déploiement (CT303 sur Proxmox)

1. Sur l'hote Proxmox (192.168.1.54, root) : `bash deploy/provision-ct303.sh` — cree et demarre CT303
   (Debian 13, non privilegie, IP DHCP). Recuperer son IP : `pct exec 303 -- ip -4 addr show eth0`.
2. Copier le code applicatif dans le conteneur sous `/opt/crypto-sim` (a l'exclusion de `.git/`,
   `.venv/`, `__pycache__/`, `.worktrees/`, `*.db`) — tant que ce depot n'est pas encore pousse sur
   `origin`, le plus simple est une archive tar transferee via `pct push` :
   ```bash
   tar --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.worktrees' \
       --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm' --exclude='.env' \
       -czf /tmp/crypto-sim.tar.gz .
   scp /tmp/crypto-sim.tar.gz root@192.168.1.54:/tmp/
   ssh root@192.168.1.54 "pct push 303 /tmp/crypto-sim.tar.gz /tmp/crypto-sim.tar.gz && \
     pct exec 303 -- mkdir -p /opt/crypto-sim && \
     pct exec 303 -- tar xzf /tmp/crypto-sim.tar.gz -C /opt/crypto-sim"
   rm -f /tmp/crypto-sim.tar.gz
   ssh root@192.168.1.54 "rm -f /tmp/crypto-sim.tar.gz && pct exec 303 -- rm -f /tmp/crypto-sim.tar.gz"
   ```
   Une fois `origin` a jour (apres ce plan), un `git clone`/`git pull` direct dans le conteneur
   remplace cette etape.
3. Copier `.env` separement (jamais dans l'archive/le depot -- l'archive de l'etape 2 l'exclut
   explicitement) : `scp .env root@192.168.1.54:/tmp/` puis
   `pct push 303 /tmp/.env /opt/crypto-sim/.env` (ecrit directement dans le conteneur, pas de fichier
   temporaire cote CT303), puis nettoyer la copie temporaire laissee sur l'hote Proxmox :
   `ssh root@192.168.1.54 "rm -f /tmp/.env"`.
4. Dans le conteneur (root) : `pct exec 303 -- bash /opt/crypto-sim/deploy/setup.sh` — installe les
   paquets systeme, cree l'utilisateur dedie `cryptosim`, le venv, initialise le schema SQLite, installe
   et active les 2 unites systemd.
5. Verifier : `pct exec 303 -- systemctl status crypto-sim.service crypto-sim-dashboard.service`,
   `pct exec 303 -- journalctl -u crypto-sim.service -n 50`, puis `curl http://<IP-CT303>:8303/` depuis
   le LAN.
6. Entree Homepage (CT104) : **non faite** — CT104 est volontairement arrete ("homepage ne me sert
   pas"), pas un oubli.

Mise a jour du code : `bash deploy/deploy-to-ct303.sh` depuis la racine du depot (commit
d'abord ; seul `HEAD` est deploye). Les etapes 2 et 3 ci-dessus ne servent qu'a la toute
premiere installation.

## Développement

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -v
```
