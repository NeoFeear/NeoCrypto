#!/usr/bin/env bash
# Deploys the committed HEAD of this repo to CT303 -- the only supported way to
# change production (review 2026-09-25: never edit /opt/crypto-sim in place).
#
#   bash deploy/deploy-to-ct303.sh            # from the repo root, on a machine with SSH to the Proxmox host
#   PVE=root@192.168.1.54 CT=303 bash deploy/deploy-to-ct303.sh
#
# Ships `git archive HEAD` (so only committed files, never .env / *.db / .venv),
# keeps a backup of the previous code in /root on CT303, runs the test suite
# inside the container, and only restarts the services if it passes.
set -euo pipefail

PVE="${PVE:-root@192.168.1.54}"
CT="${CT:-303}"
SSH_OPTS=(-o BatchMode=yes)
[ -n "${SSH_KEY:-}" ] && SSH_OPTS+=(-i "$SSH_KEY")

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Des modifications ne sont pas commitees : commite d'abord, seul HEAD est deploye." >&2
  exit 1
fi
rev="$(git rev-parse --short HEAD)"
stamp="$(date +%Y%m%d-%H%M%S)"
echo "Deploiement de $rev sur CT$CT..."

ssh "${SSH_OPTS[@]}" "$PVE" "pct exec $CT -- bash -c 'cd /opt/crypto-sim && tar --exclude=.venv --exclude=.env --exclude=\"*.db*\" --exclude=__pycache__ -czf /root/crypto-sim-before-$stamp.tgz .'"

git archive --format=tar HEAD | ssh "${SSH_OPTS[@]}" "$PVE" \
  "pct exec $CT -- bash -c 'tar -xf - --no-same-owner -C /opt/crypto-sim && chown -R cryptosim:cryptosim /opt/crypto-sim && echo $rev > /opt/crypto-sim/DEPLOYED_REVISION'"

ssh "${SSH_OPTS[@]}" "$PVE" "pct exec $CT -- bash -c 'cd /opt/crypto-sim && su -s /bin/bash cryptosim -c \".venv/bin/python -m pytest -q -p no:cacheprovider\" | tail -1'" \
  || { echo "Tests en echec sur CT$CT : services NON redemarres. Sauvegarde : /root/crypto-sim-before-$stamp.tgz" >&2; exit 1; }

ssh "${SSH_OPTS[@]}" "$PVE" "pct exec $CT -- bash -c 'systemctl restart crypto-sim crypto-sim-dashboard && sleep 5 && systemctl is-active crypto-sim crypto-sim-dashboard'"
echo "OK : $rev deploye et services redemarres (sauvegarde : /root/crypto-sim-before-$stamp.tgz)."
