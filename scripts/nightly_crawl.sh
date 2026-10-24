#!/usr/bin/env bash
# Nightly: run strangeness-crawler, then import its export into the map.
# Cron on the droplet:  17 3 * * * /opt/oddcall-map/scripts/nightly_crawl.sh >> /var/log/oddcall-crawl.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
CRAWLER_DIR="${CRAWLER_DIR:-/opt/strangeness-crawler}"
EXPORT="data/exports/latest.csv"
mkdir -p data/exports
if [ -d "$CRAWLER_DIR" ]; then
  # Adjust these two lines to the crawler's real CLI; --since uses its per-source watermarks.
  (cd "$CRAWLER_DIR" && python3 -m strangeness_crawler run --since auto && python3 -m strangeness_crawler export --csv "$OLDPWD/$EXPORT")
fi
if [ -f "$EXPORT" ]; then
  docker compose exec -T app flask --app app import-crawler /srv/exports/latest.csv
fi
docker compose exec -T app flask --app app purge
