"""Generate the synthetic demo corpus in demo_docs/.

The clinical content is deliberately written in French: it mirrors the material
of a French hospital and matches the default LLM_RESPONSE_LANGUAGE. It is
fabricated for demonstration only — no real patient data, no clinical value.

Official HAS/SFMU recommendations are a separate corpus; see
scripts/fetch_demo_docs.sh.
"""

import os
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors

os.makedirs("demo_docs", exist_ok=True)

docs = [
    {
        "filename": "demo_docs/Cardiologie_HTA_Protocole.pdf",
        "title": "PROTOCOLE - HYPERTENSION ARTÉRIELLE (HTA)",
        "content": [
            "Société Française de Cardiologie - Fiche Mémo",
            "",
            "1. Définition:",
            "L'hypertension artérielle est définie par une pression artérielle systolique (PAS)",
            ">= 140 mmHg et/ou une pression artérielle diastolique (PAD) >= 90 mmHg,",
            "mesurée en consultation.",
            "",
            "2. Objectifs tensionnels:",
            "- Pour les patients de moins de 80 ans : PAS entre 120 et 130 mmHg.",
            "- Pour les patients de plus de 80 ans : PAS < 150 mmHg sans hypotension orthostatique.",
            "- Pour les patients diabétiques ou avec maladie rénale chronique : PAS < 130 mmHg.",
            "",
            "3. Traitement de première intention:",
            "- IEC (Inhibiteurs de l'Enzyme de Conversion) ou ARA2.",
            "- Inhibiteurs calciques.",
            "- Diurétiques thiazidiques.",
            "En cas d'échec de la monothérapie, une bithérapie est recommandée."
        ]
    },
    {
        "filename": "demo_docs/Urgences_Tri_Patients.pdf",
        "title": "GUIDE DE TRI AUX URGENCES",
        "content": [
            "Recommandations de Bonnes Pratiques pour les Services d'Urgences",
            "",
            "Le tri à l'accueil des urgences permet de prioriser les patients.",
            "",
            "Niveau 1 : Urgence vitale absolue",
            "- Définition : Patient nécessitant une réanimation immédiate.",
            "- Critères : Arrêt cardio-respiratoire, coma, détresse respiratoire aiguë, choc hémorragique.",
            "- Délai de prise en charge : Immédiat (0 minute).",
            "",
            "Niveau 2 : Urgence très grave",
            "- Définition : Risque vital ou fonctionnel à très court terme.",
            "- Critères : Douleur thoracique typique, suspicion d'AVC, polytraumatisme.",
            "- Délai de prise en charge : < 20 minutes.",
            "",
            "Niveau 3 : Urgence relative",
            "- Définition : État stable mais nécessitant des examens complémentaires rapides.",
            "- Critères : Colique néphrétique, fracture d'un membre sans signe de gravité.",
            "- Délai de prise en charge : < 60 minutes."
        ]
    },
    {
        "filename": "demo_docs/Infectiologie_Maladie_Lyme.pdf",
        "title": "RECOMMANDATIONS - MALADIE DE LYME",
        "content": [
            "Prise en charge de la Borréliose de Lyme",
            "",
            "1. Érythème Migrant (Phase précoce localisée)",
            "Diagnostic clinique. La sérologie n'est pas recommandée à ce stade.",
            "",
            "2. Traitements antibiotiques recommandés (Adulte):",
            "- Première intention : Doxycycline 100 mg per os, 2 fois par jour, pendant 14 jours.",
            "- Alternative : Amoxicilline 1g per os, 3 fois par jour, pendant 14 jours.",
            "",
            "3. Cas particuliers (Femme enceinte et enfant de moins de 8 ans):",
            "- La Doxycycline est contre-indiquée.",
            "- Le traitement de première intention est l'Amoxicilline 1g, 3 fois par jour, pendant 14 jours.",
            "- En cas d'allergie aux bêtalactamines : Azithromycine.",
            "",
            "4. Prévention:",
            "Examen minutieux de la peau après chaque sortie en forêt. Retrait de la tique avec un tire-tique."
        ]
    }
]

for doc in docs:
    c = canvas.Canvas(doc["filename"], pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.darkblue)
    c.drawString(50, 800, doc["title"])
    
    c.setFont("Helvetica", 12)
    c.setFillColor(colors.black)
    y_position = 750
    for line in doc["content"]:
        if line.startswith("-"):
            c.drawString(70, y_position, line)
        elif line.startswith("1.") or line.startswith("2.") or line.startswith("3.") or line.startswith("4.") or line.startswith("Niveau"):
            c.setFont("Helvetica-Bold", 12)
            c.drawString(50, y_position, line)
            c.setFont("Helvetica", 12)
        else:
            c.drawString(50, y_position, line)
        y_position -= 20
    
    c.save()
    print(f"Generated {doc['filename']}")
