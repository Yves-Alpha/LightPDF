# Guide d’installation (poste utilisateur)

Objectif : permettre à un collègue de lancer Light-PDF (UI Streamlit uniquement) sur macOS.

## 1) Vérifier/installer Python
- Vérifier la version : `python3 --version` (OK si ≥ 3.11 ; testé avec 3.11/3.12/3.13).
- Si absent : `brew install python` (ou `brew install python@3.11`).
- S’assurer que `python3` est dans le PATH (ouvrir un nouveau terminal si besoin).

## 2) Installer Poppler (pdftoppm)
Poppler est requis par pdf2image.
```bash
brew install poppler
# Vérifier :
pdftoppm -h
```

## 2bis) Installer Ghostscript (gs)
Ghostscript est requis pour l’option de sortie vectorielle (aplat transparences sans pixellisation).
```bash
brew install ghostscript
# Vérifier :
gs --version
```

## 3) Obtenir les sources Light-PDF
Copier le dossier `LightPDF` (qui contient `packaging`) sur le poste cible, par exemple dans `~/Documents/Light-PDF`.

## 4) Lancer l’UI Streamlit (recommandé)
Depuis le dossier racine contenant `LightPDF` :
```bash
chmod +x LightPDF/packaging/run_streamlit.sh   # à faire une fois si besoin
./LightPDF/packaging/run_streamlit.sh
```
- Le premier lancement installe automatiquement les dépendances Python (`PyPDF2`, `pdf2image`, `reportlab`, `Pillow`, `streamlit`) dans `~/Library/Application Support/Light-PDF/site-packages` (connexion internet requise).
- Le navigateur s’ouvre sur l’UI. Pour lancer sans ouvrir de navigateur : `HEADLESS=true ./LightPDF/packaging/run_streamlit.sh`.
- Pour changer le port : `PORT=8502 ./LightPDF/packaging/run_streamlit.sh`.

## 5) Si les dépendances Python ne s’installent pas automatiquement
Installer manuellement dans le Python système :
```bash
python3 -m pip install --upgrade PyPDF2 pdf2image reportlab pillow streamlit
```
Puis relancer l’UI.
