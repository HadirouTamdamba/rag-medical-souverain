# Validated demonstration prompts

Every prompt below was **run against the real system** and its answer **checked against the source PDF** on 2026-08-07, on the official-only corpus (7 documents, 992 chunks), with `MIN_RELEVANCE_SCORE=0.52`. Observed timings: 12-25 s on native Ollama with the Metal GPU, Mistral 7B.

Prompts are kept **in French**: they are literal inputs, validated against French clinical documents with `LLM_RESPONSE_LANGUAGE=French`. An English translation follows each one.

**Prerequisite**: the corpus must be fetched and indexed.

```bash
./scripts/fetch_demo_docs.sh
curl -X POST http://localhost:8000/ingest/demo
curl -s http://localhost:8000/documents        # expect 7 documents
```

> ⚠️ **Golden rule**: type the prompts **exactly** as written. These phrasings were chosen because they yield a reliable answer. Measured failures are listed at the end.

The `score` column is the top retrieval score. It is the margin above `MIN_RELEVANCE_SCORE` — the higher, the more robust the question is to rephrasing.

---

## Recommended demo path (7 acts, ~10 min)

| # | Prompt (FR) | English gloss | Score | Time |
|---|---|---|---|---|
| 1 | Quel est le traitement antibiotique de première intention de la maladie de Lyme chez l'adulte ? | First-line antibiotic treatment for Lyme disease in adults? | 0.78 | 16 s |
| 2 | Quelle est la posologie de la doxycycline pour traiter un érythème migrant chez l'adulte ? | Doxycycline dosage for erythema migrans in adults? | 0.72 | 12 s |
| 3 | La sérologie de Lyme est-elle recommandée devant un érythème migrant ? | Is Lyme serology recommended with erythema migrans? | 0.85 | 13 s |
| 4 | Quel est le traitement antibiotique de première intention d'une cystite aiguë simple ? | First-line treatment for uncomplicated acute cystitis? | 0.65 | 13 s |
| 5 | Quel est l'objectif tensionnel recommandé chez l'adulte hypertendu selon la HAS ? | HAS-recommended blood pressure target in hypertensive adults? | 0.77 | 19 s |
| 6 | Quels sont les objectifs du triage en structure des urgences ? | Objectives of emergency department triage? | 0.60 | 24 s |
| 7 | Quel est le protocole de chimiothérapie pour un cancer du poumon ? | Chemotherapy protocol for lung cancer? | 0.48 → **refused** | 0.1 s |

### Expected answers

**Act 1** — doxycycline, weight-based posology, 14 days for early forms and 21 days for late forms. Source `HAS_Recommandation_Lyme_MVT_2025.pdf` p. 50.
*What to say*: the answer streams in, each statement sourced by document, page and score. Everything stays local.

**Act 2** — 200 mg per day, in one or two doses. Source `HAS_Fiche_Antibiotherapie_Lyme_2025.pdf` p. 3.
*What to say*: the figure is copied verbatim; the system prompt forbids completing numeric values from memory.

**Act 3** — no, serology is not recommended in the presence of erythema migrans (grade A). Source `HAS_Recommandation_Lyme_MVT_2025.pdf` p. 40.
*What to say*: the system returns the official recommendation together with its level of evidence.

**Act 4** — fosfomycin trometamol, single dose. Source `HAS_Synthese_Antibiotherapie_2024.pdf`.
*What to say*: we switch domain entirely with zero reconfiguration.

**Act 5** — systolic < 140 mmHg and diastolic < 90 mmHg in the general adult population, with an adapted target in older patients. Source `HAS_Fiche_Memo_HTA_Adulte_2016.pdf` pp. 52 and 56.
*What to say*: pinpointed inside a 150-page memo — this is what retrieval buys over reading.

**Act 6** — preceded by the live upload of `SFMU_Grille_FRENCH_2018_Triage.pdf` (23 chunks, ~1 s). Triage performed by a dedicated nurse, as early as possible after arrival (strong agreement). Source `SFMU_RFE_Triage_Urgences_2013.pdf` p. 7.

**Act 7** — "Je ne dispose pas de cette information dans ma base de connaissances." Instant, no sources.
*What to say*: the system refuses to answer outside its knowledge base instead of inventing.

---

## Reserve prompts (client questions)

Tested and verified, grouped by document. Useful if the client wants to dig into a topic.

### `HAS_Recommandation_Lyme_MVT_2025.pdf` (reference recommendation, 103 p.)

| Prompt (FR) | English gloss | Score | Expected answer |
|---|---|---|---|
| Quelles sont les mesures de prévention recommandées contre les piqûres de tiques ? | Recommended prevention against tick bites? | 0.63 | Stay on forest paths, keep grass short, licensed skin repellents (DEET, IR3535), clothing impregnation — all grade A. |

### `HAS_Synthese_Antibiotherapie_2024.pdf` (community antibiotic therapy, 5 p.)

| Prompt (FR) | English gloss | Score | Expected answer |
|---|---|---|---|
| Quel est le traitement antibiotique de première intention d'une cystite aiguë simple ? | First-line treatment for uncomplicated acute cystitis? | 0.65 | Fosfomycin trometamol, single dose. |

### `HAS_Fiche_Memo_HTA_Adulte_2016.pdf` (hypertension memo, 150 p.)

| Prompt (FR) | English gloss | Score | Expected answer |
|---|---|---|---|
| Quel est l'objectif tensionnel recommandé chez l'adulte hypertendu selon la HAS ? | HAS blood pressure target in hypertensive adults? | 0.77 | Systolic < 140 mmHg, diastolic < 90 mmHg; adapted in older patients. |

### `SFMU_RFE_Triage_Urgences_2013.pdf` (expert recommendations, 22 p.)

| Prompt (FR) | English gloss | Score | Expected answer |
|---|---|---|---|
| Quels sont les objectifs du triage en structure des urgences ? | Objectives of emergency department triage? | 0.60 | Triage by a dedicated nurse, as early as possible after arrival (strong agreement). |

---

## Measured failures — do not ask these live

These are reproducible on the current corpus. They are not hypothetical risks; each was observed and diagnosed.

1. **"Et en cas de contre-indication à ce traitement chez la femme enceinte ?"** (follow-up after act 1) — a **cystitis** chunk from `HAS_Synthese_Antibiotherapie_2024.pdf` enters the top 5 at 0.60, and the 7B model answers about nitrofurantoin instead of Lyme. Cross-document contamination.
   *Fix*: cross-encoder reranker, or scoping retrieval to a document subset.

2. **"Quelle est la posologie exacte de l'amoxicilline pour traiter un érythème migrant chez un enfant ?"** — the correct chunk (`HAS_Fiche_Antibiotherapie_Lyme_2025.pdf` p. 1) sits at **rank 9**, score 0.4893. The 103-page recommendation saturates the top 5 with generic passages, so with `RAG_TOP_K=5` it never reaches the context. Use act 2 instead.
   *Fix*: reranker, or a higher `RAG_TOP_K` combined with re-calibration.

3. **"Qui doit réaliser le triage aux urgences ?"** — the "who" phrasing retrieves the wrong document. Use act 6 instead, which correctly returns the triage nurse's role.

4. **Comparisons across table columns** (e.g. *"quelle différence entre tri 3A et 3B ?"*) — extraction linearises multi-column tables and a 7B model can mix columns up.
   *Fix*: table-aware extraction.

5. **Out-of-scope questions close to the corpus.** *"Quel est le protocole immunosuppresseur après une greffe rénale ?"* scores **0.5873** — above a legitimate question at 0.5591 — so it would be answered rather than refused. A similarity threshold alone is a first filter, not a reliable guard rail. See DEMO.md § 3.

---

## Transparency note (for your presentation)

These answers come from a prototype running **Mistral 7B on a laptop (M1 Pro, Metal GPU)** over 992 chunks of official publications. In production on a GPU server with a larger model (Mistral Small, Qwen 14-72B) I would expect much lower latency (not measured here) and better fidelity on tables. The failures listed above are the honest measurement of what a 7B model with a plain similarity threshold cannot do — and they define the roadmap, in order: reranker first, then table-aware extraction, then continuous automated evaluation.
