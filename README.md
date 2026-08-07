# Sovereign Medical RAG

A self-hosted RAG assistant for hospitals and healthcare organisations: local LLM, local embeddings, local vector store, a FastAPI backend and a Next.js interface. **No data ever leaves the organisation's infrastructure.**

The goal is an architecture that is sovereign, auditable and actually deployable in a hospital environment. Final regulatory compliance (HDS/GDPR in France, HIPAA elsewhere) also depends on the hosting provider, the network, governance, audit logging, authentication, security procedures and an impact assessment.

## Architecture

```
Browser ──HTTPS──▶ Ingress/TLS ──▶ Next.js frontend
                                     │  server-side relay (API key injected here)
                                     ▼
                                   FastAPI backend ──▶ Qdrant   (vectors)
                                                   └──▶ Ollama   (generation)
```

- **Local LLM:** Ollama, `mistral` by default.
- **Local embeddings:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, baked into the image — no call to HuggingFace at startup.
- **Vector store:** Qdrant, configurable collection, payload index on the document name.
- **Backend:** async FastAPI — ingestion, retrieval, generation (`/query` or SSE `/query/stream`), document lifecycle, health/readiness, Prometheus metrics.
- **Frontend:** Next.js. The browser only talks to its own origin; **the API key and the backend URL stay server-side** (`src/app/api/[...path]`).
- **Conversation memory:** recent history is passed to the LLM for follow-up questions.
- **Citations:** structured per document, page, chunk and similarity score.

## Requirements

- Docker and Docker Compose v2.
- 16 GB RAM minimum for a CPU demo; a GPU is recommended for comfortable use.
- An isolated network if real health data is involved.

## Quick start

```bash
cp .env.example .env
make up          # build and start; the Ollama model is pulled automatically
make ready       # wait until Qdrant, Ollama and the model are available
make ingest      # index demo_docs/
```

- UI: http://localhost:3000
- API docs: http://localhost:8000/docs (disabled in production)

Run `make help` for every available target.

### Two configurations

| Command | Files | Use |
|---|---|---|
| `make up` | `docker-compose.yml` + `docker-compose.override.yml` | Development: source bind-mounted, hot reload, Qdrant/Ollama/API ports published |
| `make up-prod` | `docker-compose.yml` alone | Production: fixed images, only the frontend published, Qdrant/Ollama/API on an internal network |

### Performance on macOS

Docker has no access to the Metal GPU. For answers in ~10-20 s instead of several minutes, run a native Ollama instance and point the backend at it:

```bash
OLLAMA_HOST=0.0.0.0:11435 OLLAMA_KEEP_ALIVE=60m ollama serve &
```

Then set `OLLAMA_HOST=http://host.docker.internal:11435` in `.env`. The `ollama-init` container detects the external instance and skips its own download.

### Answer language

The assistant replies in the language set by `LLM_RESPONSE_LANGUAGE` (default: `French`, matching the bundled demo corpus). Change it — and `LLM_NO_CONTEXT_ANSWER`, which bypasses the model — to serve another audience:

```bash
LLM_RESPONSE_LANGUAGE=English
LLM_NO_CONTEXT_ANSWER=I do not have that information in my knowledge base.
```

### Demo corpus

`demo_docs/` holds **synthetic** PDFs generated for the demo ([generate_pdfs.py](generate_pdfs.py)): no real health data, no clinical value. This is what `make ingest` indexes. Their clinical text is in French, matching the default answer language.

Official French recommendations (HAS, SFMU) are not redistributed here. To fetch them from the publishers:

```bash
./scripts/fetch_demo_docs.sh
```

Provenance and retrieval dates are in [demo_docs_officiels/SOURCES.md](demo_docs_officiels/SOURCES.md). The scripted client demo lives in [DEMO.md](DEMO.md); `./demo_start.sh` chains every step.

## API

Every route except `/health`, `/ready` and `/metrics` requires the `X-API-Key` header as soon as `API_KEY` is set.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness: the process responds; no dependency tested |
| `GET` | `/ready` | Readiness: Qdrant reachable, Ollama reachable **and model pulled** |
| `GET` | `/metrics` | Prometheus exposition |
| `POST` | `/query` | Question, full answer |
| `POST` | `/query/stream` | Question, answer streamed token by token over SSE |
| `POST` | `/upload` | Index a PDF (**replaces** any previous version of the same name) |
| `POST` | `/ingest/demo` | Index the demo corpus |
| `GET` | `/documents` | Inventory: document, chunk count, pages, indexing date |
| `DELETE` | `/documents/{name}` | Remove a document from the index |

`/documents` and `DELETE /documents/{name}` cover retiring an obsolete protocol and the right to erasure. Re-indexing a PDF first purges its previous chunks: without that, a revised dosage would coexist with the old one and the RAG could cite the stale value.

## Operations

### Logging

JSON logs (`LOG_JSON=true`) with a `request_id` propagated across the whole request and returned in the `X-Request-ID` header. A user reporting a wrong answer can quote that id, and the full server trace becomes findable. **No document content is ever logged.**

### Metrics

`/metrics` exposes, in Prometheus format:

| Metric | Why it matters |
|---|---|
| `medibot_http_requests_total`, `medibot_http_request_duration_seconds` | Traffic and latency per route |
| `medibot_rag_retrieval_duration_seconds` | Qdrant health |
| `medibot_rag_generation_duration_seconds` | LLM health — the first metric to degrade under load |
| `medibot_rag_queries_total{outcome}` | Share of unanswered questions (`no_context`): a direct signal of knowledge-base coverage |
| `medibot_rag_chunks_retrieved` | Recall distribution; if it collapses towards 0, revisit `MIN_RELEVANCE_SCORE` |
| `medibot_llm_inflight_requests` | Saturation of the generation semaphore |
| `medibot_llm_errors_total{kind}` | Ollama failures |
| `medibot_rate_limited_total` | Throttled clients |

Useful alerts: `medibot_llm_errors_total` rising, p95 of `medibot_rag_generation_duration_seconds` above the timeout, and an abnormally high `no_context` ratio.

### Probes

- **Liveness → `/health`.** Never point it at `/ready`: a Qdrant outage would restart perfectly healthy pods in a loop and make the incident worse.
- **Readiness → `/ready`.** It also checks that the model is actually present in Ollama; otherwise a missing model would only surface on the first question, minutes after rollout.

## Scaling

The bottleneck is **generation**, not the API.

1. **Ollama first.** A GPU cuts latency by 10-50x. Adding Ollama replicas (one GPU each) behind a Service is the most effective lever. Adding backend pods in front of a single saturated Ollama only lengthens the queue.
2. **`LLM_MAX_CONCURRENCY`** caps concurrent generations per process. On CPU, beyond 2, requests steal CPU from one another and *all* of them slow down instead of some being served properly.
3. **`API_WORKERS`** should be sized against RAM, not core count: each worker loads its own copy of the embedding model (~500 MB).
4. **Qdrant** stays single-replica until cluster mode (sharding + replication) is enabled.
5. **Rate limiting.** The application limiter is **process-local**: with N replicas the effective limit is N × `RATE_LIMIT_REQUESTS`. For a strict quota, enforce it at the Ingress (`limit-rpm`, already present in the manifests) or move to a shared Redis counter.
6. **Ingestion.** `POST /upload` indexes synchronously. Past a few dozen documents per hour, move ingestion to a dedicated task queue.
7. **Document inventory.** `/documents` aggregates by scrolling the collection, capped at 50,000 points. Beyond that, keep a separate document registry.

## Kubernetes deployment

Reference manifests live in [`deploy/k8s/`](deploy/k8s/): namespace, ConfigMap, Secret (template), Qdrant and Ollama StatefulSets, backend and frontend Deployments with HPA and PodDisruptionBudget, TLS Ingress and deny-by-default NetworkPolicies.

```bash
kubectl -n medibot create secret generic medibot-secrets \
  --from-literal=API_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
kubectl apply -k deploy/k8s
```

Adjust before any real use: domain name, image registry, storage classes, resources, and moving Ollama onto GPU nodes.

## Configuration

Every setting is documented in [.env.example](.env.example). The ones that matter most:

| Variable | Effect |
|---|---|
| `ENVIRONMENT` | In `production`, startup **fails** if `API_KEY` is shorter than 16 characters or if `ALLOWED_ORIGINS` is `*`; `/docs` is disabled |
| `API_KEY` | API authentication. Read server-side only by the frontend |
| `LLM_NUM_CTX` | Must cover prompt + context + history + answer. Below that, Ollama silently truncates the **beginning** of the prompt and loses the instructions |
| `MIN_RELEVANCE_SCORE` | Relevance threshold applied inside Qdrant. Too low: noise enters the context. Too high: the RAG wrongly answers "I don't know" |
| `LLM_RESPONSE_LANGUAGE` | Language the assistant answers in; keep `LLM_NO_CONTEXT_ANSWER` in sync |
| `EMBEDDING_MODEL` | Changing the model requires a **new collection**: the backend refuses to start against a collection of incompatible dimension |

## Development

```bash
make install   # backend venv + npm ci
make check     # ruff + pytest + eslint
```

The test suite (`backend/tests/`) covers configuration, rate limiting, metrics, the RAG pipeline (retrieval, context budget, retries, semaphore), ingestion and the API. It does not install torch: the `sentence-transformers` import is deferred and the tests use a fake embedder, which keeps CI down to a few seconds.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs lint and tests, builds both images, validates both Compose profiles, and **verifies that no API key appears in the browser bundle**.

### Unstable network during the build

If installing the Python dependencies fails from inside the Docker VM, pre-download the wheels on the host — Compose injects them as a named build context, so they never travel in the main context and never enter the final image:

```bash
make wheels
```

## Security

Already in place:

- Constant-time API key comparison; the key is never exposed to the browser.
- Rate limiting per identity (API key or IP), with `Retry-After`.
- Restricted CORS, without credentials (no cookie authentication).
- Security headers and a CSP with no external origin: the page loads no font, no CDN and no remote telemetry.
- Internal errors are never returned to the client (a traceback can contain excerpts of medical documents).
- Non-root containers, `no-new-privileges`, read-only filesystems under Kubernetes.
- Strict input validation; PDFs are size-capped and deleted from disk after indexing.

Still to address before real health data:

- A TLS reverse proxy with organisational authentication: OIDC, SSO or an internal IAM. The API key is a shared secret, not a user identity — it provides neither per-user traceability nor individual revocation.
- Named audit logging (who queried what), without medical content in clear text.
- A retention and deletion policy for the Qdrant collections.
- Knowledge segmentation per organisation, department or tenant.
- Validation of incoming documents: provenance, version, date, owning department.
- A data protection impact assessment and a security review.

## Clinical limitations

This system assists search across internal documents. It does not replace clinical judgement, must not produce recommendations outside the retrieved context, and must remain supervised by healthcare professionals. It is not a medical device under EU Regulation 2017/745.

## Licence

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
