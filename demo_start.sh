#!/bin/bash
# Full startup for the MediBot RAG demo (see DEMO.md).
# Usage: ./demo_start.sh
set -euo pipefail
cd "$(dirname "$0")"

OLLAMA_PORT=11435
API=http://localhost:8000
BOLD=$(tput bold 2>/dev/null || true); RESET=$(tput sgr0 2>/dev/null || true)
step() { echo "${BOLD}==> $1${RESET}"; }

# The API key is optional for a local demo; use it if .env defines one.
API_KEY=$(grep -E '^API_KEY=' .env 2>/dev/null | cut -d= -f2- || true)
AUTH=()
[ -n "${API_KEY:-}" ] && AUTH=(-H "X-API-Key: ${API_KEY}")

step "1/5 Native Ollama instance (Metal GPU) on port ${OLLAMA_PORT}"
if curl -s -m 2 "http://127.0.0.1:${OLLAMA_PORT}/api/version" > /dev/null 2>&1; then
  echo "    Already running."
else
  OLLAMA_HOST="0.0.0.0:${OLLAMA_PORT}" OLLAMA_KEEP_ALIVE=60m nohup ollama serve \
    > /tmp/ollama_demo.log 2>&1 &
  echo "    Started (logs: /tmp/ollama_demo.log)."
  until curl -s -m 2 "http://127.0.0.1:${OLLAMA_PORT}/api/version" > /dev/null 2>&1; do sleep 1; done
fi

step "2/5 Docker stack (Qdrant, backend, frontend)"
# Development mode: docker-compose.override.yml publishes ports 8000/6333.
docker compose up -d

step "3/5 Waiting for the backend"
until curl -s -m 3 "${API}/health" > /dev/null 2>&1; do sleep 2; done

step "4/5 Warming up the model (may take ~1 min on first run)"
curl -s -m 300 "http://127.0.0.1:${OLLAMA_PORT}/api/generate" -d \
  '{"model":"mistral","prompt":"Bonjour","stream":false,"keep_alive":"60m","options":{"num_ctx":4096}}' > /dev/null

step "5/5 Readiness check"
# /ready fails until the model is present in the targeted Ollama instance.
curl -s -m 30 "${API}/ready"
echo
echo
echo "${BOLD}Ready.${RESET} UI: http://localhost:3000 — Demo script: DEMO.md"
echo "Indexed documents: curl -s ${API}/documents ${AUTH[*]:-}"
echo "Remember to ask a warm-up question before the client arrives."
