# Light-PDF

Outil métier interne pour préparer les adaptations clients à partir des PDF HD imprimeur. Il recadre au format fini, organise les pages selon les noms de production et génère la version de livraison choisie.

## Parcours utilisateur

- **Qualité d’origine** : recadrage au format fini, sans allègement destructif.
- **Version écran** : optimisation sûre qui conserve le texte et les éléments PDF.
- **Version très légère** : pages aplaties pour réduire fortement le poids.
- **Intranet G20** : conserve la meilleure qualité possible sous 50 Mo ; une réduction forte vise 48 Mo pour garder une marge, et Acrobat reste le dernier recours.

## Règles de production

- Les suites `_01`, `_02`, `_03` peuvent être assemblées dans l’ordre numérique.
- Une version suffixée `_COR`, `-COR` ou ` COR` remplace automatiquement la page d’origine.
- Les pages manquantes, corrections concurrentes et mélanges ambigus sont signalés avant traitement.
- Les fichiers temporaires et non PDF sont ignorés.
- Le format fini vient en priorité des informations déjà présentes dans le PDF. Aucun retrait arbitraire de 5 mm n’est appliqué si elles sont absentes.

## Sécurité des images

Les versions courantes ne réécrivent plus les images internes une par une. Cette règle protège les fichiers CMJN, profils colorimétriques et transparences rencontrés dans les corpus BUREAU VALLEE, G20 et FRANCAP, et évite le rendu en négatif observé auparavant.

## Dépendances

- Python 3.11+
- Modules Python : `pikepdf`, `Pillow`, `pdf2image`, `reportlab`, `streamlit`.
- Poppler (`pdftoppm`) pour les versions très légères et le rattrapage automatique DIAPAR (`brew install poppler`).
- qpdf pour l’optimisation sans perte (`brew install qpdf`).

## Usage

```bash
streamlit run streamlit_app.py
```

## Déploiement Streamlit Cloud

1. Fichiers requis : `streamlit_app.py`, `app.py`, `requirements.txt`, `packages.txt`, `.streamlit/config.toml`.
2. Sur Streamlit Cloud : **New app** → choisir le repo/branche → indiquer `streamlit_app.py` comme fichier principal.
3. Dépendances Python via `requirements.txt`, binaires système via `packages.txt` (`poppler-utils`, `qpdf`).
4. Test local :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```
