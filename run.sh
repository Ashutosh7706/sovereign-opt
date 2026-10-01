#!/usr/bin/env sh
# Start the Sovereign Optimization Platform on http://127.0.0.1:8000  (or HTTPS with --https)
# First boot prints a one-time admin PIN and writes backend/data/BOOTSTRAP_ADMIN.txt.
set -e
python -m pip install -r requirements.txt
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
if [ "$1" = "--https" ]; then
  [ -f certs/server.crt ] || { python -m pip install cryptography; python backend/scripts/make_cert.py; }
  export SOVEREIGN_HTTPS=1
  exec python -m uvicorn app:app --app-dir backend --host "$HOST" --port "$PORT" \
       --ssl-keyfile certs/server.key --ssl-certfile certs/server.crt --timeout-graceful-shutdown 30
fi
exec python -m uvicorn app:app --app-dir backend --host "$HOST" --port "$PORT" --timeout-graceful-shutdown 30
