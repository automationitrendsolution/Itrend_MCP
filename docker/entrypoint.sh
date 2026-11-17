#!/bin/sh
# Start-up for the iTrend MCP container: apply database changes, run the monitor
# in the background (restarting it if it stops), then run the website.
set -e

python manage.py migrate --noinput

(
  while true; do
    python manage.py monitor_mcps || true
    echo "monitor stopped; restarting in 5 seconds" >&2
    sleep 5
  done
) &

# One worker: the website keeps track of the MCP processes it started.
exec gunicorn config.wsgi \
  --bind 0.0.0.0:8000 \
  --worker-class gthread --workers 1 --threads 8 \
  --timeout 3600 \
  --access-logfile -
