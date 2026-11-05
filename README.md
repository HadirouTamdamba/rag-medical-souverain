# MediBot — a sovereign medical RAG assistant

[![CI](https://github.com/HadirouTamdamba/rag-medical-souverain/actions/workflows/ci.yml/badge.svg)](https://github.com/HadirouTamdamba/rag-medical-souverain/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

Ask a question about a clinical guideline. You get an answer that cites its document and page, or a plain *"I don't know"*. Everything runs on your own hardware: local LLM, local embeddings, local vector store. No cloud API, no data leaving the machine.

## Why I built this

I wanted to know what it really takes to build a retrieval-augmented assistant that a hospital's security team could say yes to, not just one that demos well. So I built the whole chain (model, embeddings, vector store, API, UI, deployment) and then spent most of my time trying to break it.

**This is a prototype, not a product.** I have not deployed it in a hospital and it has seen no real patient data. What I can show is a working system, the numbers I measured on it, and an honest list of where it fails. The failures are the part I find most useful, and they are written down below rather than hidden.

Nothing in the design is specific to medicine. Documents that cannot leave the building, answers that must cite their source, a system that has to be able to say "I don't know": that describes most regulated document work. What is specific to a domain is the corpus and the calibrated relevance threshold.

## What it looks like

The demo corpus is seven official French publications (HAS and SFMU), so questions and answers are in French. English glosses in brackets.

> **Q:** La sérologie de Lyme est-elle recommandée devant un érythème migrant ? *(Is Lyme serology recommended when erythema migrans is present?)*
>
> **A:** Not recommended (grade A). Source: `HAS_Recommandation_Lyme_MVT_2025.pdf`, page 40. *~13 s on a laptop.*

> **Q:** Quel est le protocole de chimiothérapie pour un cancer du poumon ? *(Chemotherapy protocol for lung cancer?)*
>
> **A:** *"I don't have this information in my knowledge base."* No sources. *0.1 s.*

The second one is the answer I care about most. It comes back in a tenth of a second because the vector search finds nothing relevant enough, so the language model is never even called.

The answer language is a setting (`LLM_RESPONSE_LANGUAGE`), not hard-coded.

## What I measured

All of this was measured on one MacBook (M1 Pro) with Ollama running natively on the Metal GPU. These are single runs on a demo machine, not benchmarks.

| | |
|---|---|
| Corpus | 7 official publications, 992 chunks, indexed in about 53 s |
| Time to answer | 12–25 s with Mistral 7B |
| Vector search | roughly 75–110 ms |
| Time to refuse an out-of-scope question | 0.1 s |
| Backend image | 2.94 GB (it was 9.67 GB, see below) |
| Tests | 94, plus a 4-job CI pipeline |

## What measuring taught me

Building the demo was the easy part. These are the things I found once I stopped admiring it and started trying to break it, by reading the code critically, by measuring, and by asking it questions it should not be able to answer.

**1. My first version leaked its own API key.** I had put it in a `NEXT_PUBLIC_*` variable. Next.js inlines those into the JavaScript bundle, so any user could read the key in their browser and call the API directly. The browser now only talks to its own origin, and a small server-side relay adds the key. A CI step fails the build if a key ever shows up in the client bundle again.

**2. The image carried 3.6 GB of GPU libraries it could never use.** The PyTorch wheel on PyPI declares the whole CUDA stack as dependencies. This image runs on CPU. Installing the explicit CPU build took it from 9.67 GB to 2.94 GB, and a build-time assertion now breaks the build if CUDA comes back.

**3. Re-indexing a revised protocol left the old version behind.** Both versions then sat in the index side by side, so the assistant could quote a superseded dosage. Re-indexing now purges the previous chunks first, and there is a test for it.

**4. A database outage looked like a bug in my code.** When Qdrant was unreachable the API answered `500`, which tells whoever is on call to go and look for a code defect. It now answers `503` for a down dependency and `500` only when the fault is mine.

**5. A similarity threshold is not a reliable way to refuse.** This is the one I am most glad I caught. I calibrated the relevance threshold at 0.52: legitimate questions score above it, my out-of-scope test question scored below. Then I tried a second out-of-scope question, about immunosuppression after a kidney transplant. It scored **0.5873**, above a perfectly legitimate question at 0.5591. A threshold on its own will happily answer things it should refuse. It is a first filter, not a guard rail. I have not fixed this yet (see the roadmap) but I have documented it and the calibration method in [DEMO.md](DEMO.md).

## Where it fails

I would rather you read these here than discover them in a demo.

- **Cross-document contamination.** On a follow-up question about pregnancy, a chunk about *cystitis* from another document entered the context and the 7B model answered about nitrofurantoin instead of Lyme. Reproducible.
- **The right passage buried.** A precise paediatric dosage sat at rank 9. The 103-page recommendation fills the top 5 results with generic passages, so the correct chunk never reaches the model. When I swapped my small synthetic test documents for the real corpus (61 chunks to 992), two of my own demo questions broke for exactly this reason.
- **Multi-column tables.** PDF extraction flattens them, and the model can mix up the columns.
- **Clumsy phrasing from a small model.** For the serology question above it actually wrote *"Yes, serology is not recommended"*. The substance and the citation were right, the opening word was not. That is a 7B model for you.
- **Out-of-scope questions that sit close to the corpus** can slip past the threshold (see point 5).

## What is verified, and what is not

| Verified | Not verified |
|---|---|
| 94 unit and API tests | **Kubernetes manifests.** They render with `kustomize` but I have never applied them to a live cluster |
| CI: lint, tests, both Docker images built, both Compose profiles validated | **Behaviour under load.** The concurrency cap and rate limiter are unit-tested, not load-tested |
| End-to-end demo on a laptop with Docker Compose | **GPU performance.** I expect an order-of-magnitude gain, I have not measured it |
| Non-root containers, security headers, no key in the browser bundle | **Any real clinical data**, and any independent security review |

This is not an HDS or GDPR certification. Compliance depends on the hosting provider, the network, authentication, audit logging and a data protection impact assessment, none of which a codebase can settle on its own.

## Run it

You need Docker with Compose v2 and about 16 GB of RAM.

```bash
git clone https://github.com/HadirouTamdamba/rag-medical-souverain.git
cd rag-medical-souverain
cp .env.example .env
./scripts/fetch_demo_docs.sh   # downloads the official PDFs from HAS and SFMU
make up                        # builds the images and pulls the Mistral model (about 4 GB)
make ready                     # re-run until it reports everything is up
make ingest                    # indexes the corpus, about 50 s
```

Then open http://localhost:3000. `make help` lists everything else.

The first `make up` takes a while: it builds two images and downloads a few gigabytes. On a Mac, Docker cannot reach the GPU, so answers from the containerised Ollama can take minutes. For 10–20 s answers, run Ollama natively and point the backend at it:

```bash
OLLAMA_HOST=0.0.0.0:11435 OLLAMA_KEEP_ALIVE=60m ollama serve &
# then in .env:  OLLAMA_HOST=http://host.docker.internal:11435
```

The official PDFs are **not** in this repository. They belong to their publishers, so the script fetches them from the source. Provenance is in [demo_docs_officiels/SOURCES.md](demo_docs_officiels/SOURCES.md). The scripted demo and its validated questions are in [DEMO.md](DEMO.md) and [DEMO_PROMPTS.md](DEMO_PROMPTS.md).

## How it fits together

```
Browser ──HTTPS──▶ Ingress/TLS ──▶ Next.js frontend
                                     │  server-side relay (API key injected here)
                                     ▼
                                   FastAPI backend ──▶ Qdrant   (vectors)
                                                   └──▶ Ollama   (generation)
```

A few choices worth explaining, because the reasons matter more than the list:

- **The embedding model ships inside the Docker image.** A hospital network may have no route to HuggingFace, and a system that needs the internet to start is not sovereign.
- **Liveness and readiness are separate probes.** If liveness depended on Qdrant, a database outage would restart perfectly healthy API pods in a loop and make the incident worse.
- **LLM concurrency is capped.** On CPU, beyond roughly two simultaneous generations, requests steal the processor from one another and *all* of them slow down. Capping it and queuing the rest serves people better.
- **Retries only happen before the first token.** Replaying after a partial answer would produce duplicated, incoherent text.
- **Production refuses to start misconfigured.** With `ENVIRONMENT=production`, a missing or weak API key or a wildcard CORS policy stops the boot, rather than quietly weakening security.
- **A refusal costs almost nothing.** Retrieval decides before generation, which is why "I don't know" takes 0.1 s.

## What comes next

In the order the measurements justify, not the order that sounds most exciting:

1. **A cross-encoder reranker.** It addresses the two retrieval failures above and the threshold problem in one move.
2. **Table-aware PDF extraction.** Dosages live in tables.
3. **Continuous evaluation.** A question set validated by clinicians, replayed on every change to the corpus, so regressions show up before a user finds them.
4. **SSO and a named audit log.** The API key is a shared secret, not a user identity, so it cannot tell you who asked what.

<details>
<summary><strong>API reference</strong></summary>

Every route except `/health`, `/ready` and `/metrics` requires the `X-API-Key` header once `API_KEY` is set.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness: the process responds, no dependency tested |
| `GET` | `/ready` | Readiness: Qdrant reachable, Ollama reachable **and the model pulled** |
| `GET` | `/metrics` | Prometheus exposition |
| `POST` | `/query` | Question, full answer |
| `POST` | `/query/stream` | Question, answer streamed token by token over SSE |
| `POST` | `/upload` | Index a PDF (**replaces** any previous version of the same name) |
| `POST` | `/ingest/demo` | Index the demo corpus |
| `GET` | `/documents` | Inventory: document, chunks, pages, indexing date |
| `DELETE` | `/documents/{name}` | Remove a document from the index |

`/documents` and the delete route cover retiring an obsolete protocol and the right to erasure.

</details>

<details>
<summary><strong>Operations: logs, metrics, probes</strong></summary>

**Logs.** JSON (`LOG_JSON=true`), with a `request_id` carried across the whole request and returned in the `X-Request-ID` header. If a user reports a wrong answer, the id leads to the full server trace. No document content is ever logged.

**Metrics** at `/metrics`, in Prometheus format:

| Metric | Why it matters |
|---|---|
| `medibot_http_requests_total`, `medibot_http_request_duration_seconds` | Traffic and latency per route |
| `medibot_rag_retrieval_duration_seconds` | Qdrant health |
| `medibot_rag_generation_duration_seconds` | LLM health, the first thing to degrade under load |
| `medibot_rag_queries_total{outcome}` | Share of unanswered questions (`no_context`): a direct signal of how well the knowledge base covers real use |
| `medibot_rag_chunks_retrieved` | Recall distribution; if it collapses towards 0, revisit the threshold |
| `medibot_llm_inflight_requests` | Saturation of the generation cap |
| `medibot_llm_errors_total{kind}` | Ollama failures |
| `medibot_rate_limited_total` | Throttled clients |

Useful alerts: LLM errors rising, p95 generation time above the timeout, and an unusually high `no_context` ratio.

**Probes.** Liveness points at `/health`, readiness at `/ready`. Never swap them.

</details>

<details>
<summary><strong>Scaling notes</strong></summary>

The bottleneck is **generation**, not the API.

1. **The LLM first.** A GPU is the obvious lever, and adding Ollama replicas behind a Service after that. I expect the gain to be large but have not measured it. Adding backend pods in front of one saturated Ollama only lengthens the queue.
2. **`LLM_MAX_CONCURRENCY`** caps concurrent generations per process.
3. **`API_WORKERS`** should be sized against RAM, not cores: each worker loads its own copy of the embedding model (about 500 MB).
4. **Qdrant** stays single-replica until cluster mode (sharding and replication) is enabled.
5. **Rate limiting** is process-local: with N replicas the effective limit is N times the configured value. For a strict quota, enforce it at the Ingress (a `limit-rpm` annotation is already in the manifests) or move to a shared Redis counter.
6. **Ingestion** is synchronous. Past a few dozen documents an hour, it belongs behind a task queue.
7. **The document inventory** scrolls the collection and is capped at 50,000 points. Beyond that, keep a separate registry.

</details>

<details>
<summary><strong>Kubernetes (reference manifests, not yet run on a cluster)</strong></summary>

[`deploy/k8s/`](deploy/k8s/) holds 19 resources: namespace, ConfigMap, a Secret template, Qdrant and Ollama StatefulSets, backend and frontend Deployments with an HPA and PodDisruptionBudgets, a TLS Ingress and deny-by-default NetworkPolicies.

```bash
kubectl -n medibot create secret generic medibot-secrets \
  --from-literal=API_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
kubectl apply -k deploy/k8s
```

They pass `kubectl kustomize`, but as said above I have not applied them to a live cluster. Treat them as a starting point. Before real use, adjust the domain, image registry, storage classes and resources, and move Ollama onto GPU nodes.

</details>

<details>
<summary><strong>Configuration</strong></summary>

Every setting is documented in [.env.example](.env.example). The ones that matter most:

| Variable | Effect |
|---|---|
| `ENVIRONMENT` | In `production`, startup **fails** if `API_KEY` is under 16 characters or `ALLOWED_ORIGINS` is `*`, and `/docs` is disabled |
| `API_KEY` | API authentication, read server-side only by the frontend |
| `LLM_NUM_CTX` | Must cover prompt, context, history and answer. Below that, Ollama silently truncates the **start** of the prompt and the instructions are lost. This one cost me real debugging time |
| `MIN_RELEVANCE_SCORE` | Where the system switches from answering to refusing. The code default is 0.35; `.env.example` ships 0.52, calibrated on the demo corpus. It is corpus-specific: adding or removing a document shifts every score, so recalibrate (method in [DEMO.md](DEMO.md) § 3) |
| `LLM_RESPONSE_LANGUAGE` | Language of the answers. Keep `LLM_NO_CONTEXT_ANSWER` in the same language: it bypasses the model |
| `EMBEDDING_MODEL` | Changing it needs a **new collection**; the backend refuses to start against one with a different dimension |

</details>

<details>
<summary><strong>Development</strong></summary>

```bash
make install   # backend venv + npm ci
make check     # ruff + pytest + eslint
```

The tests do not install torch. The `sentence-transformers` import is deferred and a fake embedder stands in for it, which keeps the suite to a second or so. CI runs lint and tests, builds both images, validates both Compose profiles, and checks that no API key appears in the browser bundle.

If installing Python dependencies fails inside the Docker VM on an unreliable network, `make wheels` pre-downloads them on the host. Compose injects them as a named build context, so they never enter the main context or the final image.

</details>

<details>
<summary><strong>Security: what is in place, and what is not</strong></summary>

In place:

- Constant-time API key comparison, and the key is never exposed to the browser.
- Rate limiting per identity (API key, else IP), with `Retry-After`.
- Restricted CORS without credentials, since there is no cookie authentication.
- Security headers and a CSP with no external origin: no remote font, CDN or telemetry.
- Internal errors are never returned to the client, because a traceback can contain excerpts of medical documents.
- Non-root containers, `no-new-privileges`, read-only filesystems under Kubernetes.
- Strict input validation. PDFs are size-capped and deleted from disk after indexing.

Still to do before any real health data:

- A TLS reverse proxy with organisational authentication (OIDC, SSO or an internal IAM).
- A named audit log of who queried what, with no medical content in clear text.
- A retention and deletion policy for the Qdrant collections.
- Knowledge segmentation per organisation, department or tenant.
- Provenance and version checks on incoming documents.
- A data protection impact assessment and an independent security review.

</details>

## Clinical limitations

This system assists search across documents. It does not replace clinical judgement, must not produce recommendations outside the retrieved context, and has to stay under the supervision of healthcare professionals. It is not a medical device under EU Regulation 2017/745.

## Licence and contact

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Built by **Hadirou Tamdamba**, an AI and MLOps consultant working on sovereign AI for healthcare. If you want to talk about the findings, the failures or how this applies to your own documents: [github.com/HadirouTamdamba](https://github.com/HadirouTamdamba) · hadirousalif21@outlook.fr
