# Roto PDF Converter (standalone)

Petit outil en ligne de commande pour transformer des PDF HD (impression roto) en PDF sans traits de coupe ni fonds perdus, avec deux niveaux de compression (HQ et Light).

## Dépendances
- Python 3.11+ disponible sur la machine (`python3` dans le PATH).  
- Modules Python : `PyPDF2`, `pdf2image`, `reportlab`, `Pillow`.  
  Le launcher Platypus (ou `app.py` lui-même) installe automatiquement ces modules dans `~/Library/Application Support/<NomDeVotreApp>/site-packages` si besoin.
- Poppler pour `pdf2image` (`pdftoppm` dans le PATH, ex : `brew install poppler`).
- Ghostscript (`gs`) pour l’option de sortie vectorielle qui aplatit les transparences sans pixelliser (`brew install ghostscript`).

## Usage
Lancer l’UI Streamlit :
```bash
./LightPDF/packaging/run_streamlit.sh
```
ou utiliser l’app macOS générée avec Platypus.

L’option "Sortie vectorielle" de l’UI produit un PDF intermédiaire qui conserve les vecteurs et se contente d’aplatir les transparences (nécessite Ghostscript).
