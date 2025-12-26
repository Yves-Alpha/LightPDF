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

## Déploiement Streamlit Cloud
1. Fichiers à pousser : `streamlit_app.py`, `app.py`, `requirements.txt`, `README.md` (+ dossiers utiles comme `packaging/`, `script/`). Évite les `.app`/archives ou fichiers générés.
2. Sur Streamlit Cloud : **New app** → choisis le repo/branche → indique `streamlit_app.py` comme fichier principal.
3. Les dépendances Python sont installées via `requirements.txt`.
4. Poppler (`pdftoppm`) et Ghostscript sont des binaires système optionnels :
   - Sans Ghostscript, l’aplat vectoriel et la compression vectorielle ne fonctionnent pas.
   - Sans Poppler, le profil raster (`pdf2image`) ne fonctionne pas.
   Consulte les logs Streamlit Cloud pour vérifier leur présence.
5. Test local rapide :
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   streamlit run streamlit_app.py
   ```
