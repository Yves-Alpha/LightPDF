#!/usr/bin/env bash
set -euo pipefail

# Launcher for the Platypus app bundle (Streamlit only).
# - Uses system python3
# - Déclenche le run_streamlit.sh après vérifications

HERE="$(cd "$(dirname "$0")" && pwd)"          # .../Contents/Resources/LightPDF/packaging
RUN_STREAMLIT="$HERE/run_streamlit.sh"
BUNDLE_DIR="$(cd "$HERE/../../../.." && pwd 2>/dev/null || true)"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
APP_NAME="${ROTO_APP_NAME:-}"
if [[ -z "$APP_NAME" && -n "$BUNDLE_DIR" && "$BUNDLE_DIR" == *.app ]]; then
  APP_NAME="$(basename "$BUNDLE_DIR" .app)"
fi
APP_NAME="${APP_NAME:-Light-PDF}"
export ROTO_APP_NAME="$APP_NAME"

if [[ ! -x "$RUN_STREAMLIT" ]]; then
  if [[ -f "$RUN_STREAMLIT" ]]; then
    chmod +x "$RUN_STREAMLIT"
  else
    echo "[LightPDF] Erreur: run_streamlit.sh introuvable." >&2
    exit 1
  fi
fi

exec "$RUN_STREAMLIT" "$@"
