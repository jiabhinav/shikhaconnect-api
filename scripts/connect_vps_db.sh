#!/bin/sh
# Forward local port 5433 to PostgreSQL on the VPS without exposing it publicly.
set -eu
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: sh scripts/connect_vps_db.sh USER@VPS_IP [SSH_PORT]" >&2
    exit 2
fi
case "$1" in
    -*|'') echo "Invalid SSH destination" >&2; exit 2 ;;
esac
exec ssh -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -p "${2:-22}" \
    -L 127.0.0.1:5433:127.0.0.1:5432 \
    "$1"
