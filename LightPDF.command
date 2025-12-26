#!/usr/bin/env bash
# Lance l'UI Streamlit de Light-PDF sans compilation (double-clic sur macOS).
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
export ROTO_APP_NAME="Light-PDF"

if [[ -x "$APP_ROOT/packaging/run_streamlit.sh" ]]; then
  RUN="$APP_ROOT/packaging/run_streamlit.sh"
elif [[ -x "$APP_ROOT/LightPDF/packaging/run_streamlit.sh" ]]; then
  RUN="$APP_ROOT/LightPDF/packaging/run_streamlit.sh"
else
  echo "[Light-PDF] Script packaging/run_streamlit.sh introuvable à partir de $APP_ROOT" >&2
  exit 1
fi

exec "$RUN" "$@"
