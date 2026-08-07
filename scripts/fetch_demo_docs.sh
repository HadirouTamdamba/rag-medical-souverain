#!/bin/bash
# Fetch the official HAS/SFMU documents used by the demo.
#
# These PDFs are not versioned: they belong to their publishers and remain
# downloadable from the source. Full provenance and retrieval dates are in
# demo_docs_officiels/SOURCES.md.
#
# Usage: ./scripts/fetch_demo_docs.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DEST=demo_docs_officiels
mkdir -p "$DEST"

fetch() {
  local name=$1 url=$2
  if [ -s "$DEST/$name" ]; then
    echo "  = $name (already present)"
    return
  fi
  if curl -fsSL -m 120 -A "Mozilla/5.0" -o "$DEST/$name.part" "$url"; then
    mv "$DEST/$name.part" "$DEST/$name"
    echo "  + $name"
  else
    rm -f "$DEST/$name.part"
    # Publisher URLs move when a recommendation is updated: check SOURCES.md and
    # search for the document on the publisher's website.
    echo "  ! failed: $name — check the URL in $DEST/SOURCES.md" >&2
  fi
}

echo "Downloading the official documents into $DEST/"

fetch "HAS_Recommandation_Lyme_MVT_2025.pdf" \
  "https://www.has-sante.fr/upload/docs/application/pdf/2025-02/borreliose_de_lyme_et_autres_maladies_vectorielles_a_tiques_mvt_-_recommandations.pdf"
fetch "HAS_Fiche_Antibiotherapie_Lyme_2025.pdf" \
  "https://www.has-sante.fr/upload/docs/application/pdf/2025-02/fiche_pratique_-_antibiotherapie_recommandee_dans_la_borreliose_de_lyme.pdf"
fetch "HAS_Guide_Parcours_Suspicion_Lyme_2022.pdf" \
  "https://www.has-sante.fr/upload/docs/application/pdf/2022-03/guide_parcours_suspicion_borreliose_de_lyme_mel.pdf"
fetch "HAS_Fiche_Memo_HTA_Adulte_2016.pdf" \
  "https://www.has-sante.fr/upload/docs/application/pdf/2016-10/fiche_memo_rapport_elaboration_hta_mel.pdf"
fetch "HAS_Synthese_Antibiotherapie_2024.pdf" \
  "https://www.has-sante.fr/upload/docs/application/pdf/2024-08/choix_et_durees_dantibiotherapie_synthese_actualisation_aout_2024__mel_v2.pdf"
fetch "SFMU_Grille_FRENCH_2018_Triage.pdf" \
  "https://www.sfmu.org/upload/referentielsSFMU/2018%2006%2010%20FRENCH%20pour%20PDF%20A4%20V1.1%20site.pdf"
fetch "SFMU_RFE_Triage_Urgences_2013.pdf" \
  "https://www.sfmu.org/upload/referentielsSFMU/rfe_triage2013.pdf"

echo
echo "Done. To index: make ingest — then see DEMO.md for the demo script."
