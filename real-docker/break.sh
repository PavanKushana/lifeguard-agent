#!/usr/bin/env bash
# break.sh: break the real shop in one of 3 ways, then let Lifeguard-Agent find and fix it.
#   bash break.sh bad-deploy    deploy api 2.3.2, which stops at once (a new setting nobody filled in)
#   bash break.sh cache-crash   crash Redis with a segfault; it has no restart policy, so it stays down
#   bash break.sh low-memory    set the api's memory limit to 16 MB; it gets killed again and again
#   bash break.sh reset         put the whole shop back to normal
# Deploys and config changes go into changes.log, like on a real team. A crash doesn't:
# nobody changed anything, it just happened.
set -euo pipefail
cd "$(dirname "$0")"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> changes.log; }

case "${1:-}" in
  bad-deploy)
    API_IMAGE=lifeguard/real-api:2.3.2 docker compose up --detach --no-deps api
    log "Deployed api 2.3.2 (lifeguard/real-api:2.3.2, was 2.3.1): faster product search"
    ;;
  cache-crash)
    # A real segfault (signal 11), what a Redis bug looks like: Redis writes its crash report and stops.
    # "docker kill --signal SEGV" doesn't work here: Redis runs as the container's first process,
    # and Linux ignores a signal like that once Redis hands it back.
    # The command loses its connection when Redis dies, so its error is expected.
    docker exec cache redis-cli DEBUG SEGFAULT || true
    ;;
  low-memory)
    docker update --memory 16m --memory-swap 16m api
    log "Changed the api memory limit from 512 MB to 16 MB to save memory on the server"
    ;;
  reset)
    docker compose up --detach --force-recreate --wait
    log "Reset the shop: web nginx:1.27-alpine, api 2.3.1, db postgres:16-alpine, cache redis:7.4-alpine"
    ;;
  *)
    echo "Usage: bash break.sh bad-deploy | cache-crash | low-memory | reset"
    exit 1
    ;;
esac

sleep 5
docker ps --all --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
