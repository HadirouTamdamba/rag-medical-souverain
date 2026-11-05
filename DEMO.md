# Demonstration script

> 📋 The full list of validated prompts, the reserve questions and the phrasings to avoid is in **[DEMO_PROMPTS.md](DEMO_PROMPTS.md)**.

The corpus is made **exclusively of official publications** (HAS, SFMU), fetched from the publishers with `./scripts/fetch_demo_docs.sh`. Nothing is authored or synthesised for this repository.

Prompts are kept **in French**: they are literal inputs, validated against French clinical documents with `LLM_RESPONSE_LANGUAGE=French`. An English translation follows each one.

Every act below was **re-measured end to end** on 2026-08-07 (Apple M1 Pro, native Ollama on the Metal GPU, Mistral 7B, `num_ctx=4096`, `MIN_RELEVANCE_SCORE=0.52`) against the 7 official documents: 992 chunks. Observed timings: 12-25 s with a warm model.

## 1. Pre-demo checklist (15 minutes before)

```bash
# 1. Native Ollama instance (Metal GPU — essential, far faster than Docker)
OLLAMA_HOST=0.0.0.0:11435 OLLAMA_KEEP_ALIVE=60m ollama serve &

# 2. Fetch the official corpus (once)
./scripts/fetch_demo_docs.sh

# 3. Application stack
docker compose up -d

# 4. Check that everything is green
curl http://localhost:8000/ready

# 5. Index the corpus (~50 s for 992 chunks)
curl -X POST http://localhost:8000/ingest/demo

# 6. Warm up the model (repeat after > 60 min of inactivity)
curl -s http://127.0.0.1:11435/api/generate -d \
  '{"model":"mistral","prompt":"Bonjour","stream":false,"keep_alive":"60m","options":{"num_ctx":4096}}' > /dev/null

# 7. Remove the triage grid so it can be uploaded live during act 6
curl -X DELETE http://localhost:8000/documents/SFMU_Grille_FRENCH_2018_Triage.pdf
```

Open http://localhost:3000 and ask a warm-up question before the client arrives.

## 2. The script (7 acts, ~10 min)

### Act 1 — Simple question
> **Quel est le traitement antibiotique de première intention de la maladie de Lyme chez l'adulte ?**
>
> *First-line antibiotic treatment for Lyme disease in adults?*

Doxycycline, weight-based posology, 14 days for early forms and 21 days for late forms `[HAS_Recommandation_Lyme_MVT_2025.pdf page 50]`. Top score 0.78, ~16 s.

**What to say**: the answer streams in, and every statement is sourced by document, page and similarity score. No data leaves the machine.

### Act 2 — Precise numeric value
> **Quelle est la posologie de la doxycycline pour traiter un érythème migrant chez l'adulte ?**
>
> *Doxycycline dosage to treat erythema migrans in adults?*

*200 mg per day, in one or two doses* `[HAS_Fiche_Antibiotherapie_Lyme_2025.pdf page 3]`. Top score 0.72, ~12 s.

**What to say**: the figure is copied verbatim from the official fact sheet; the system prompt forbids completing numeric values from memory.

### Act 3 — A recommendation with its level of evidence
> **La sérologie de Lyme est-elle recommandée devant un érythème migrant ?**
>
> *Is Lyme serology recommended when erythema migrans is present?*

*No — serology is not recommended in the presence of erythema migrans (grade A)* `[HAS_Recommandation_Lyme_MVT_2025.pdf page 40]`. Top score 0.85 (the best of the whole script), ~13 s.

### Act 4 — Another specialty: community antibiotic therapy
> **Quel est le traitement antibiotique de première intention d'une cystite aiguë simple ?**
>
> *First-line antibiotic treatment for uncomplicated acute cystitis?*

*Fosfomycin trometamol, single dose* `[HAS_Synthese_Antibiotherapie_2024.pdf]`. Top score 0.65, ~13 s.

**What to say**: we switch domain entirely with zero reconfiguration.

### Act 5 — Cardiology, inside a 150-page document
> **Quel est l'objectif tensionnel recommandé chez l'adulte hypertendu selon la HAS ?**
>
> *HAS-recommended blood pressure target in hypertensive adults?*

*Systolic < 140 mmHg and diastolic < 90 mmHg in the general adult population*, with the adapted target in older patients `[HAS_Fiche_Memo_HTA_Adulte_2016.pdf pages 52 and 56]`. Top score 0.77, ~19 s.

**What to say**: the answer is pinpointed inside a 150-page memo — this is what retrieval buys over reading.

### Act 6 — The centrepiece: live upload + national guideline
1. Upload the **SFMU FRENCH 2018 triage grid** live (paperclip in the UI) → indexed in ~1 s, "23 chunks added". Say: *"I have just ingested the national emergency triage guideline — a document full of complex tables — in a second."*
2. Then ask:

> **Quels sont les objectifs du triage en structure des urgences ?**
>
> *What are the objectives of triage in an emergency department?*

*Triage performed by a dedicated nurse, as early as possible after arrival (strong agreement)* `[SFMU_RFE_Triage_Urgences_2013.pdf page 7]`. Top score 0.60, ~24 s.

### Act 7 — The anti-hallucination close (end here)
> **Quel est le protocole de chimiothérapie pour un cancer du poumon ?**
>
> *What is the chemotherapy protocol for lung cancer?*

*"Je ne dispose pas de cette information dans ma base de connaissances."* — **instant** (0.1 s: no chunk passes the relevance threshold, so the LLM is never called), no sources.

**What to say**: the system refuses to answer outside its document base instead of inventing — that is the precondition for trust in a clinical setting.

## 3. Calibrating `MIN_RELEVANCE_SCORE`

This threshold is the single setting that decides between *"answers"* and *"refuses"*. It is **corpus-specific**: adding or removing a document changes every score, so it must be re-measured after any change to the knowledge base.

The method takes two minutes:

```bash
docker exec medibot_backend python - <<'PY'
import rag_pipeline as rp
from qdrant_client import models

must_find = ["<a question that MUST be answered>", "..."]
must_reject = ["<an out-of-scope question that MUST be refused>", "..."]

def top(q):
    r = rp.get_qdrant_client().query_points(
        collection_name=rp.COLLECTION_NAME, query=rp.embed_query(q), limit=1,
        search_params=models.SearchParams(hnsw_ef=rp.settings.qdrant_hnsw_ef))
    return r.points[0].score

floor = max(top(q) for q in must_reject)   # highest score among refusals
ceiling = min(top(q) for q in must_find)   # lowest score among expected answers
print(f"valid window: ]{floor:.4f} ; {ceiling:.4f}]  -> pick {(floor + ceiling) / 2:.2f}")
PY
```

Set the threshold in the middle of the window. On the current corpus the floor is 0.4813 and the ceiling 0.5591, hence **`MIN_RELEVANCE_SCORE=0.52`**.

**Two caveats worth stating to a client.** A narrow window means the corpus is at the limit of what a similarity threshold can separate: it works, but it is not robust. And the window can vanish entirely — on this corpus, the out-of-scope question *"Quel est le protocole immunosuppresseur après une greffe rénale ?"* scores **0.5873**, above a legitimate question at 0.5591. A score threshold alone is therefore **not** a reliable refusal mechanism; it is a first filter. The real fix is a cross-encoder reranker on top of retrieval, which is the first item on the quality roadmap.

## 4. Questions to AVOID live

Measured failures on this corpus. Knowing them avoids unpleasant surprises — and feeds the roadmap discussion.

1. **Follow-up on pregnancy** (*"Et en cas de contre-indication à ce traitement chez la femme enceinte ?"*) — a **cystitis** chunk from `HAS_Synthese_Antibiotherapie_2024.pdf` enters the top 5 at 0.60 and the 7B model latches onto it, answering about nitrofurantoin. Cross-document contamination, reproducible. Fix: reranker, or scoping retrieval to a document set.

2. **Amoxicillin dosage in children** (*"Quelle est la posologie exacte de l'amoxicilline pour traiter un érythème migrant chez un enfant ?"*) — the correct chunk (`HAS_Fiche_Antibiotherapie_Lyme_2025.pdf` page 1) sits at **rank 9** with a score of 0.4893: the 103-page recommendation saturates the top 5 with generic passages. Use act 2 (doxycycline in adults) instead, which is stable.

3. **Comparisons across table columns** (e.g. *"quelle différence entre tri 3A et 3B ?"*) — extraction linearises multi-column tables and a 7B model can mix columns up. Roadmap item: table-aware extraction.

4. Any question outside the indexed documents triggers the refusal — that is intended, but save it for act 7.

## 5. If a client asks "what about production?"

- **Hardware**: this runs on an M1 Pro laptop; on a GPU server (L40S/A100) I expect answers in a few seconds rather than tens of seconds, and larger models (Mistral Small, Qwen 14-72B) become viable. That gain is an expectation, not something I have measured.
- **Sovereignty**: LLM, embeddings and vector store are 100% local; Mistral is a French vendor.
- **Quality roadmap**, in the order the measurements justify: a cross-encoder reranker (fixes failures 1 and 2 above), table-aware PDF extraction, continuous automated evaluation on a domain-validated question set, SSO, audit logs, multi-tenancy.
- **A war story worth telling**: the initial hallucinations came from a silent prompt truncation (Ollama's default context window), caught by metrics and systematic testing. Then, on switching to the full official corpus, two demo questions regressed — caught the same way. That is the MLOps job: instrument, measure, correct.

## 6. Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Very slow answer (> 2 min) | Model unloaded, or backend pointed at the Docker Ollama | Re-run the warm-up; check `OLLAMA_HOST` in `.env` |
| "Language model service unavailable" | Native instance on 11435 not running | Re-run step 1 of the checklist |
| Refusal on a legitimate question | `MIN_RELEVANCE_SCORE` above that question's top score | Re-run the calibration in section 3 |
| Answer drifts to another specialty | Cross-document contamination in the top-k | Known limitation, see section 4 |
| Frontend unresponsive | Backend restarted | `docker compose up -d`, then check `/ready` |
