#!/bin/bash
# ══════════════════════════════════════════════════
# Light-PDF – Lanceur local (double-clic pour ouvrir)
# ══════════════════════════════════════════════════
# Ouvre l'interface Streamlit dans votre navigateur.
# Utilise votre Ghostscript Homebrew (10.06) au lieu
# de la version buggée de Debian.
# ══════════════════════════════════════════════════

cd "$(dirname "$0")"

# Vérifier les dépendances Homebrew
echo "🔍 Vérification des dépendances..."
MISSING=""
command -v gs >/dev/null 2>&1 || MISSING="$MISSING ghostscript"
command -v pdftoppm >/dev/null 2>&1 || MISSING="$MISSING poppler"
command -v qpdf >/dev/null 2>&1 || MISSING="$MISSING qpdf"

if [ -n "$MISSING" ]; then
    echo "⚠️  Dépendances manquantes :$MISSING"
    echo "   Installation automatique via Homebrew..."
    brew install $MISSING
fi

# Activer le venv s'il existe, sinon le créer
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "📦 Création de l'environnement Python..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
fi

# Vérifier que streamlit est installé
if ! python -c "import streamlit" 2>/dev/null; then
    echo "📦 Installation des dépendances Python..."
    pip install -r requirements.txt
fi

echo ""
echo "🪶 Light-PDF démarre..."
echo "   GS: $(gs --version) ($(which gs))"
echo "   qpdf: $(qpdf --version 2>&1 | head -1)"
echo "   Python: $(python --version)"
echo ""
echo "   L'interface s'ouvre dans votre navigateur."
echo "   Pour arrêter : Ctrl+C ou fermez cette fenêtre."
echo ""

# Lancer Streamlit
streamlit run streamlit_app.py \
    --server.headless=false \
    --server.port=8501 \
    --browser.gatherUsageStats=false
