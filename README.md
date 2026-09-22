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

**Note de synchronisation :** le `config.yaml` (et la logique associee — RSI/ATR
adaptatifs pour `dca`, grille Fibonacci auto-adaptative pour `grid`, plusieurs paires en
parallele) a evolue directement sur CT303 depuis le dernier commit pousse sur ce depot ;
le `config.yaml` versionne ici reflete encore la version mono-symbole du Plan 6. A
resynchroniser explicitement avant de se fier au depot comme source de verite pour le
comportement live actuel.

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

Mise a jour du code : refaire l'etape 2 (ou `git pull` une fois `origin` a jour), puis
`pct exec 303 -- systemctl restart crypto-sim.service crypto-sim-dashboard.service`.

## Développement

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest -v
```
