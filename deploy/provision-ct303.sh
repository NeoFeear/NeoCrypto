#!/usr/bin/env bash
# deploy/provision-ct303.sh -- run on the PROXMOX HOST (192.168.1.54, as
# root), NOT inside a container, to create CT303 for the crypto-sim
# deployment. Safe to re-run: exits early (success) if CT303 already
# exists instead of erroring, so a partial/repeated run is harmless.
#
# TEMPLATE_STORAGE/ROOTFS_STORAGE/BRIDGE below assume this host's actual
# storage/network names -- verify with `pvesm status` and `ip link` before
# running for real if this host's config ever differs from what's recorded
# in memory (storage local + local-lvm, bridge vmbr0, as of 2026-09-15).
set -euo pipefail

CTID=303
CT_HOSTNAME=crypto-sim
TEMPLATE_STORAGE=local
TEMPLATE=debian-13-standard_13.6-1_amd64.tar.zst
ROOTFS_STORAGE=local-lvm
BRIDGE=vmbr0
DISK_GB=8
RAM_MB=768
CORES=1

if pct status "$CTID" >/dev/null 2>&1; then
  if ! pct status "$CTID" | grep -q running; then
    pct start "$CTID"
  fi
  echo "CT$CTID existe deja (demarre si necessaire), rien d'autre a faire." >&2
  exit 0
fi

if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  pveam update
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" \
  --unprivileged 1 \
  --features nesting=0 \
  --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
  --rootfs "${ROOTFS_STORAGE}:${DISK_GB}" \
  --memory "$RAM_MB" \
  --cores "$CORES" \
  --onboot 1 \
  --start 1 \
  --timezone Europe/Paris

echo "CT$CTID cree et demarre. Verifier l'IP attribuee avec :"
echo "  pct exec $CTID -- ip -4 addr show eth0"
