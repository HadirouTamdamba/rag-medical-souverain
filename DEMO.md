# Demonstration script — Sovereign Medical RAG

> 📋 The full list of validated prompts (with expected answers, grouped by document, plus the phrasings to avoid) is in **[DEMO_PROMPTS.md](DEMO_PROMPTS.md)**.

The questions below are kept **in French**: they are literal inputs, validated against French clinical documents (HAS, SFMU) with `LLM_RESPONSE_LANGUAGE=French`. An English translation follows each one. To run the demo in English, switch the corpus and the answer language (see README, *Answer language*).

Every question in this script was **tested and validated** on the reference machine (Apple M1 Pro, native Ollama on the Metal GPU, Mistral 7B, `num_ctx=4096`) on 2026-07-15. Observed timings: 10-21 s per answer with a warm model.

## 1. Pre-demo checklist (15 minutes before)

```bash
# 1. Native Ollama instance (Metal GPU — essential, 40x faster than Docker)
OLLAMA_HOST=0.0.0.0:11435 OLLAMA_KEEP_ALIVE=60m ollama serve &

# 2. Application stack
docker compose up -d

# 3. Check that everything is green
curl http://localhost:8000/ready
# Expected: {"status":"ok","qdrant_connected":true,"ollama_connected":true,...}

# 4. Warm up the model (repeat after > 60 min of inactivity)
curl -s http://127.0.0.1:11435/api/generate -d \
  '{"model":"mistral","prompt":"Bonjour","stream":false,"keep_alive":"60m","options":{"num_ctx":4096}}' > /dev/null

# 5. If the store is empty, re-index the demo documents
curl -X POST http://localhost:8000/ingest/demo
```

Open http://localhost:3000 and ask any warm-up question before the client arrives.

**Official documents**: the 7 PDFs from the French national platforms (HAS, SFMU) are fetched by `./scripts/fetch_demo_docs.sh` into [demo_docs_officiels/](demo_docs_officiels/); provenance is in [SOURCES.md](demo_docs_officiels/SOURCES.md).

- Index **before** the demo if the store is empty: `HAS_Fiche_Antibiotherapie_Lyme_2025.pdf` (via the paperclip in the UI).
- Keep back **for the live upload** (act 5): `SFMU_Grille_FRENCH_2018_Triage.pdf`.

## 2. The script (6 acts, ~10 min)

### Act 1 — Simple question, internal document
> **Quel est le traitement antibiotique de première intention de la maladie de Lyme chez l'adulte ?**
>
> *First-line antibiotic treatment for Lyme disease in adults?*

Validated answer: *doxycycline 100 mg orally, twice daily, for 14 days*, with a double citation `[HAS_Fiche_Antibiotherapie_Lyme_2025.pdf page 1]` and `[Infectiologie_Maladie_Lyme.pdf page 1]`. (~14 s)

**What to say**: the answer streams in, and every statement is sourced by document, page and similarity score. No data leaves the machine.

### Act 2 — Follow-up question (conversation memory)
> **Et en cas de contre-indication à ce traitement chez la femme enceinte ?**
>
> *And if that treatment is contraindicated, in a pregnant patient?*

Validated answer: *amoxicillin is recommended* — the system resolves "that treatment" to doxycycline from the conversation history. (~16 s)

**What to say**: the assistant follows the thread of the conversation, like a junior doctor being asked successive questions.

### Act 3 — Another specialty: cardiology
> **Quels sont les objectifs tensionnels pour un patient hypertendu de moins de 80 ans ?**
>
> *Target blood pressure for a hypertensive patient under 80?*

Validated answer: *systolic 120-130 mmHg* `[Cardiologie_HTA_Protocole.pdf page 1]`. (~14 s)

### Act 4 — Emergency department: triage criteria
> **Quels sont les critères du niveau 1 de tri aux urgences et le délai de prise en charge ?**
>
> *Level 1 triage criteria in the emergency department, and the time to care?*

Validated answer: *cardiac arrest, coma, acute respiratory distress, haemorrhagic shock — immediate care (0 minutes)* `[Urgences_Tri_Patients.pdf page 1]`. (~12 s)

### Act 5 — The centrepiece: live upload of a national guideline + tabular data
1. Upload the **SFMU FRENCH 2018 triage grid** live (paperclip in the UI) → indexed in ~5 s, "23 chunks added". Say: *"I have just ingested the national emergency triage guideline — a document full of complex tables — in a few seconds."*
2. Then ask the **validated** tabular question about the HAS fact sheet:

> **Quelle est la posologie exacte de l'amoxicilline pour traiter un érythème migrant chez un enfant ?**
>
> *Exact amoxicillin dosage to treat erythema migrans in a child?*

Validated answer: *50 mg/kg/day, not exceeding 4 g/day, in 3 doses every 8 h; if the 8 h interval cannot be respected, 25 mg/kg every 12 h* — extracted from a **table** in the 2025 HAS fact sheet, with citations on pages 1 and 3. (~21 s)

**What to say**: this value comes from a multi-column dosage table — the hardest case for a document system.

### Act 6 — The anti-hallucination close (end here)
> **Quel est le protocole de chimiothérapie pour un cancer du poumon ?**
>
> *What is the chemotherapy protocol for lung cancer?*

Validated answer: *"Je ne dispose pas de cette information dans ma base de connaissances."* — **instant** (0 s: no chunk passes the relevance threshold, so the LLM is never called), no sources.

**What to say**: the system refuses to answer outside its document base instead of inventing — that is the precondition for trust in a clinical setting.

## 3. Questions to AVOID live

- **Comparisons across columns of a table** (e.g. "what distinguishes triage 3A from 3B?", "what are the delays for triage 2?" on the FRENCH grid): extraction linearises multi-column tables and a 7B model can mix columns up. This is a roadmap item (table-aware extraction), not something to improvise during a demo.
- Any question outside the indexed documents will trigger the refusal — that is intended, but save it for act 6.
- If the client wants to ask their own question, steer them towards the Lyme / hypertension / emergency triage topics.

## 4. If a client asks "what about production?" (your consulting value)

- **Hardware**: this runs on an M1 Pro laptop; in an organisation, a GPU server (L40S/A100) brings answers down to 1-3 s and allows larger, more reliable models (Mistral Small, Qwen 14-72B).
- **Sovereignty**: LLM, embeddings and vector store are 100% local — compatible with HDS requirements; Mistral is a French vendor.
- **Quality roadmap**: table-aware PDF extraction, a reranker, continuous automated evaluation (a domain-validated question/answer set), SSO authentication, audit logs, multi-tenancy per department.
- **A war story worth telling**: the initial hallucinations came from a silent prompt truncation (Ollama's default context window), caught by metrics and systematic testing. That is exactly the MLOps job: instrument, measure, correct.

## 5. Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Very slow answer (> 2 min) | Model unloaded, or backend pointed at the Docker Ollama | Re-run the warm-up; check `OLLAMA_HOST` in `.env` is `http://host.docker.internal:11435` |
| "Language model service unavailable" | Native instance on 11435 not running | Re-run step 1 of the checklist |
| Refusal on a legitimate question | `MIN_RELEVANCE_SCORE` too high for that phrasing | Rephrase using the document's own terms |
| Frontend unresponsive | Backend restarted | `docker compose up -d`, then check `/ready` |
