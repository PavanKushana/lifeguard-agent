#!/usr/bin/env bash
# setup.sh: build the shop api in both versions and start the real shop.
#   bash setup.sh
# 2.3.1 is the good version; 2.3.2 is the broken one that break.sh bad-deploy deploys.
# Every change to the shop is written to changes.log, the shop's deploy history. The agent reads it
# with its recent_changes tool, like a team's #deploys channel.
set -euo pipefail
cd "$(dirname "$0")"

# One line in the deploy history, with the server's clock time.
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> changes.log; }

docker build --quiet --tag lifeguard/real-api:2.3.1 --build-arg APP_VERSION=2.3.1 api
docker build --quiet --tag lifeguard/real-api:2.3.2 --build-arg APP_VERSION=2.3.2 api
docker compose up --detach --wait
log "Started the shop: web nginx:1.27-alpine, api 2.3.1, db postgres:16-alpine, cache redis:7.4-alpine"

echo
docker ps --all --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
echo
# On the very first start Postgres sets up its database for a few seconds, and until then the api
# answers 503 (27 Oct, first run on the VM). So ask every 2 seconds, for up to 30 seconds.
for _ in $(seq 15); do
  answer=$(curl --silent --max-time 5 http://localhost/api/health || true)
  [[ "$answer" == *'"ok"'* ]] && break
  sleep 2
done
echo "Shop check: $answer"
