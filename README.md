# Light-PDF

Outil web pour optimiser des PDF HD (impression roto) : suppression traits de coupe et fonds perdus, compression des images embarquées sans pixellisation du texte ni des vecteurs.

## Moteur

- **pikepdf** : crop (TrimBox → MediaBox), recompression JPEG in-place, merge. Zéro corruption — texte, vecteurs, polices, transparences restent intacts.
- Trois profils : **Nettoyer** (crop seul), **Moyen** (q55, échelle 70%), **Très légers** (q30, échelle 35%).

## Dépendances

- Python 3.11+
- Modules Python : `pikepdf`, `Pillow`, `pdf2image`, `reportlab`, `streamlit`.
- Poppler (`pdftoppm`) pour le profil raster uniquement (`brew install poppler`).

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
