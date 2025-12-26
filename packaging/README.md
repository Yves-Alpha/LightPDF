# Packaging (Platypus)

Ce dossier contient tout le nécessaire pour générer une app macOS via Platypus.

## 1) Compiler avec Platypus (GUI)
1. Ouvrez Platypus et chargez le profil `LightPDF/packaging/Platypus/Roto_Converter.platypus`.
2. Vérifiez/ajustez :
   - `Script Path` : `LightPDF/packaging/run_streamlit.sh`
   - `Bundled Files` : `LightPDF`
   - `Interface` : None, `Runs in background` coché. (Pas besoin de drag & drop)
3. Build l’app.

## 2) Compiler en CLI
Si Platypus CLI est installé :
```bash
platypus -P LightPDF/packaging/Platypus/Roto_Converter.platypus "LightPDF.app"
```

## 3) Comment sont gérées les dépendances Python
- Aucun venv embarqué.
- Au premier lancement, le script `run_streamlit.sh` vérifie les modules (`streamlit`, `PyPDF2`, `pdf2image`, `reportlab`, `Pillow`). S’ils manquent, il les installe dans `~/Library/Application Support/<NomDeVotreApp>/site-packages` (le nom est déduit du bundle `.app`). L’app elle-même refait ce contrôle si on lance `app.py` directement.
- `pdftoppm` (poppler) doit être présent dans le PATH (Homebrew : `brew install poppler`). Un avertissement est affiché si absent.

## 4) Usage de l’app
- Double-cliquez l’app : le navigateur s’ouvre automatiquement sur l’UI Streamlit LightPDF.
- Vous pouvez aussi lancer depuis le terminal : `./LightPDF.app/Contents/Resources/LightPDF/packaging/run_streamlit.sh`

## 5) Icône
`IconPath` est vide dans le profil. Ajoutez une icône `.icns` si désiré et mettez à jour `IconPath`.

## 6) UI Streamlit (Light-PDF)
- Script : `LightPDF/streamlit_app.py`
- Lanceur pratique (auto-install deps) : `LightPDF/packaging/run_streamlit.sh`
  ```bash
  ./LightPDF/packaging/run_streamlit.sh
  ```
  Cela installe Streamlit + dépendances dans `~/Library/Application Support/<NomApp>/site-packages` et démarre l’UI.
 - Par défaut HEADLESS=false et le navigateur s’ouvre via `BROWSER=open`. Vous pouvez forcer sans ouverture : `HEADLESS=true ./LightPDF/packaging/run_streamlit.sh`.
 - Port configurable : `PORT=8502 ./LightPDF/packaging/run_streamlit.sh`
 - Sinon : `python3 -m streamlit run LightPDF/streamlit_app.py` (nécessite streamlit installé globalement).
