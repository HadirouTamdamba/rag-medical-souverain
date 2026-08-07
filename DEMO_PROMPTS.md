# Validated demonstration prompts

Every prompt below was **tested against the real system** and its answer **verified against the source PDF** (2026-07-15). Observed timings: 6-25 s on native Ollama with the Metal GPU, Mistral 7B.

Prompts are kept **in French**: they are literal inputs, validated against French clinical documents with `LLM_RESPONSE_LANGUAGE=French`. An English translation follows each one, so the demo remains readable to an international audience.

**Prerequisite**: all 9 documents must be indexed. Check with `curl -s http://localhost:8000/ready`. To (re-)index the official documents, upload them through the paperclip in the UI, or:

```bash
./scripts/fetch_demo_docs.sh
for f in demo_docs_officiels/*.pdf; do
  curl -s -X POST http://localhost:8000/upload -F "file=@$f;type=application/pdf"; echo
done
```

> ⚠️ **Golden rule**: type the prompts **exactly** as written. These phrasings were chosen because they yield a reliable answer. Fragile variants are listed at the end (see *What to avoid*).

---

## Recommended demo path (8 acts, ~10 min)

### Act 1 — Simple question + cross-document citations
**Prompt:**
> Quel est le traitement antibiotique de première intention de la maladie de Lyme chez l'adulte ?
>
> *First-line antibiotic treatment for Lyme disease in adults?*

**Expected answer:** doxycycline as first line (200 mg/day in adults), from the 2025 HAS recommendation.
**Sources:** `HAS_Recommandation_Lyme_MVT_2025.pdf`, `Infectiologie_Maladie_Lyme.pdf`.
**What to say:** the answer streams in, each statement sourced (document + page + score). Everything stays local.

### Act 2 — Conversation memory (ask RIGHT AFTER act 1, without rephrasing)
**Prompt:**
> Et en cas de contre-indication à ce traitement chez la femme enceinte ?
>
> *And if that treatment is contraindicated, in a pregnant patient?*

**Expected answer:** amoxicillin or cefuroxime as first line (doxycycline being contraindicated).
**What to say:** the system resolves "that treatment" to doxycycline from the conversation history.

### Act 3 — Prevention (excellent relevance score: 0.85)
**Prompt:**
> La sérologie de Lyme est-elle recommandée devant un érythème migrant ?
>
> *Is Lyme serology recommended when erythema migrans is present?*

**Expected answer:** No — serology is not recommended in the presence of erythema migrans (grade A).
**Source:** `HAS_Recommandation_Lyme_MVT_2025.pdf` page 40.
**What to say:** diagnosing erythema migrans is clinical — the system returns the official recommendation together with its level of evidence.

### Act 4 — Another specialty: community antibiotic therapy
**Prompt:**
> Quel est le traitement antibiotique de première intention d'une cystite aiguë simple ?
>
> *First-line antibiotic treatment for uncomplicated acute cystitis?*

**Expected answer:** fosfomycin trometamol, single dose.
**Source:** `HAS_Synthese_Antibiotherapie_2024.pdf`.
**What to say:** we switch domain entirely (urology / community infectious disease) with zero reconfiguration.

### Act 5 — Emergency department: triage criteria
**Prompt:**
> Quels sont les critères du niveau 1 de tri aux urgences et le délai de prise en charge ?
>
> *Level 1 triage criteria in the emergency department, and the time to care?*

**Expected answer:** cardiac arrest, coma, acute respiratory distress, haemorrhagic shock — immediate care (0 minutes).
**Source:** `Urgences_Tri_Patients.pdf` page 1.

### Act 6 — National guideline: how triage is organised
**Prompt:**
> Quels sont les objectifs du triage en structure des urgences ?
>
> *What are the objectives of triage in an emergency department?*

**Expected answer:** triage performed by a dedicated triage nurse (IOA), as early as possible after arrival at reception (recommendations with strong agreement).
**Source:** `SFMU_RFE_Triage_Urgences_2013.pdf`.
**What to say:** an official document from the French Society of Emergency Medicine.

### Act 7 — The centrepiece: live upload + tabular data
1. Upload `demo_docs_officiels/SFMU_Grille_FRENCH_2018_Triage.pdf` **live** (paperclip) → "23 chunks added" in ~5 s.
   *Say: "I am ingesting the national triage guideline right now — a document full of complex tables."*
2. Then ask a question whose answer sits inside a **table** of the HAS fact sheet:

> Quelle est la posologie exacte de l'amoxicilline pour traiter un érythème migrant chez un enfant ?
>
> *Exact amoxicillin dosage to treat erythema migrans in a child?*

**Expected answer:** 50 mg/kg/day, not exceeding 4 g/day, in 3 doses every 8 h.
**Source:** `HAS_Fiche_Antibiotherapie_Lyme_2025.pdf`.
**What to say:** this value is extracted from a multi-column dosage table — the hardest case for a document system.

### Act 8 — Anti-hallucination (end here)
**Prompt:**
> Quel est le protocole de chimiothérapie pour un cancer du poumon ?
>
> *What is the chemotherapy protocol for lung cancer?*

**Expected answer:** "Je ne dispose pas de cette information dans ma base de connaissances." — **instant**, no sources.
**What to say:** the system refuses to answer outside its knowledge base instead of inventing. That is the precondition for trust in a clinical setting.

---

## Additional validated prompts (reserve / client questions)

Grouped by document, all tested and verified. Useful if the client wants to dig into a topic.

### `HAS_Recommandation_Lyme_MVT_2025.pdf` (reference recommendation, 103 p.)

| Prompt (FR) | English gloss | Expected answer |
|---|---|---|
| Quelles sont les mesures de prévention recommandées contre les piqûres de tiques ? | Recommended prevention against tick bites? | Stay on paths, licensed skin repellents, garden maintenance (grade A). |
| Quand faut-il orienter un patient vers un centre de référence des maladies vectorielles à tiques ? | When to refer to a tick-borne disease referral centre? | Persistent symptoms after correct treatment, or a difficult diagnosis. |
| Quelle est la posologie de la doxycycline pour traiter un érythème migrant chez l'adulte ? | Doxycycline dosage for erythema migrans in adults? | 200 mg/day in 2 doses. |

### `HAS_Synthese_Antibiotherapie_2024.pdf` (community antibiotic therapy, 5 p.)

| Prompt (FR) | English gloss | Expected answer |
|---|---|---|
| Quel est le traitement antibiotique de première intention d'une cystite aiguë simple ? | First-line treatment for uncomplicated acute cystitis? | Fosfomycin trometamol, single dose. |

### `HAS_Fiche_Memo_HTA_Adulte_2016.pdf` (hypertension memo, 150 p.)

| Prompt (FR) | English gloss | Expected answer |
|---|---|---|
| Quel est l'objectif tensionnel recommandé chez l'adulte hypertendu selon la HAS ? | HAS-recommended blood pressure target in hypertensive adults? | Systolic < 140 mmHg and diastolic < 90 mmHg (general population); adapted targets in older patients. |

### `SFMU_RFE_Triage_Urgences_2013.pdf` (expert recommendations, 8 p.)

| Prompt (FR) | English gloss | Expected answer |
|---|---|---|
| Quels sont les objectifs du triage en structure des urgences ? | Objectives of emergency department triage? | Triage by a dedicated triage nurse, performed as early as possible (strong agreement). |

### Internal demonstration documents (`demo_docs/`)

| Prompt (FR) | English gloss | Expected answer |
|---|---|---|
| Quels sont les critères du niveau 1 de tri aux urgences et le délai de prise en charge ? | Level 1 triage criteria and time to care? | Cardiac arrest, coma, respiratory distress, haemorrhagic shock — immediate (0 min). |
| Quel est le traitement de la maladie de Lyme chez la femme enceinte ? | Lyme disease treatment in pregnancy? | Amoxicillin 1 g three times daily for 14 days (doxycycline contraindicated). |

---

## What to avoid in a live demo (fragile phrasings and known traps)

These cases failed or gave imperfect answers during testing. Knowing them avoids unpleasant surprises — and feeds your consulting narrative about the roadmap.

1. **"Qui doit réaliser le triage aux urgences ?"** (*Who should perform triage?*) — the "who" phrasing retrieved the wrong document and answered incorrectly ("attending physician" instead of nurse). **Use instead** the "objectives of triage" question (act 6), which correctly returns the triage nurse's role.

2. **"Comment réaliser une automesure selon la règle des 3 ?"** (*How to self-measure blood pressure under the "rule of 3"?*) — partially invented answer; the exact content sits in a poorly extracted box. Do not ask.

3. **"Quelle est la durée d'antibiothérapie pour une pneumonie communautaire ?"** (*Antibiotic duration for community-acquired pneumonia?*) — the information exists in the document but was not retrieved reliably. Do not ask.

4. **Blood pressure targets "under 80"** — since the 2016 HAS memo was indexed, this question draws on **two documents with different targets** (the internal protocol says 120-130 mmHg; HAS 2016 says < 140 mmHg). The system can conflate them. **Use instead** "blood pressure target according to HAS" (hypertension reserve), or remove the internal protocol if you want a single answer. *This is itself an excellent talking point: document governance — versions, duplicates, the authoritative source — is a real deployment concern.*

5. **Comparisons across table columns** (e.g. "what is the difference between triage 3A and 3B?", "response times for triage 2?") — extraction linearises multi-column tables and a 7B model can mix columns up. Roadmap item: *table-aware* extraction.

---

## Transparency note (for your presentation)

These answers come from a prototype running **Mistral 7B on a laptop (M1 Pro, CPU/Metal GPU)**. In production on a GPU server with a larger model (Mistral Small, Qwen 14-72B) you gain: 1-3 s latency, better fidelity on tables, and an automated evaluation pipeline (a domain-validated question/answer set) that guarantees no regression on every documentation update.
