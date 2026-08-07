#!/bin/sh
# uvicorn startup, parameterised from the environment.
set -eu

# Each worker loads its own copy of the embedding model (~500 MB RSS).
# Size API_WORKERS against available RAM, not against the core count.
WORKERS="${API_WORKERS:-1}"

set -- uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "$WORKERS" \
    --timeout-keep-alive 65 \
    --no-access-log

# The application access log already carries request id, duration and status
# (main.py). Behind a reverse proxy, uvicorn must read X-Forwarded-* so the
# logged client IP is the real one.
if [ "${TRUST_PROXY_HEADERS:-false}" = "true" ]; then
    set -- "$@" --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}"
fi

exec "$@"
