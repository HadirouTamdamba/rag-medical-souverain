# Provenance of the official demonstration documents

Public documents retrieved on 2026-07-15 from the French national health
platforms. Re-check periodically: recommendations are revised over time.

| File | Publisher | Year | Source |
|---|---|---|---|
| HAS_Recommandation_Lyme_MVT_2025.pdf | HAS | 2025 | https://www.has-sante.fr/upload/docs/application/pdf/2025-02/borreliose_de_lyme_et_autres_maladies_vectorielles_a_tiques_mvt_-_recommandations.pdf |
| HAS_Fiche_Antibiotherapie_Lyme_2025.pdf | HAS | 2025 | https://www.has-sante.fr/upload/docs/application/pdf/2025-02/fiche_pratique_-_antibiotherapie_recommandee_dans_la_borreliose_de_lyme.pdf |
| HAS_Guide_Parcours_Suspicion_Lyme_2022.pdf | HAS | 2022 | https://www.has-sante.fr/upload/docs/application/pdf/2022-03/guide_parcours_suspicion_borreliose_de_lyme_mel.pdf |
| HAS_Fiche_Memo_HTA_Adulte_2016.pdf | HAS | 2016 | https://www.has-sante.fr/upload/docs/application/pdf/2016-10/fiche_memo_rapport_elaboration_hta_mel.pdf |
| HAS_Synthese_Antibiotherapie_2024.pdf | HAS / SPILF | 2024 | https://www.has-sante.fr/upload/docs/application/pdf/2024-08/choix_et_durees_dantibiotherapie_synthese_actualisation_aout_2024__mel_v2.pdf |
| SFMU_Grille_FRENCH_2018_Triage.pdf | SFMU | 2018 | https://www.sfmu.org/upload/referentielsSFMU/2018%2006%2010%20FRENCH%20pour%20PDF%20A4%20V1.1%20site.pdf |
| SFMU_RFE_Triage_Urgences_2013.pdf | SFMU | 2013 | https://www.sfmu.org/upload/referentielsSFMU/rfe_triage2013.pdf |

Use in the demo (see [DEMO.md](../DEMO.md)):
- `HAS_Fiche_Antibiotherapie_Lyme_2025.pdf` — index it BEFORE the demo (acts 1, 2 and 5).
- `SFMU_Grille_FRENCH_2018_Triage.pdf` — upload it LIVE in front of the client (act 5).
- The others — material to widen the knowledge base or answer an unplanned question on another topic.

## Versioning

These PDFs are **not** versioned in the repository: they belong to their
publishers (HAS, SFMU) and remain freely downloadable from the URLs above.
To rebuild the corpus locally:

```bash
./scripts/fetch_demo_docs.sh
```
