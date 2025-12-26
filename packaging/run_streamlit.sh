#!/usr/bin/env bash
set -euo pipefail

# Helper to launch the Streamlit UI without requiring a global install.
# It installs deps into ~/Library/Application Support/<AppName>/site-packages.

HERE="$(cd "$(dirname "$0")" && pwd)"          # .../packaging
BUNDLE_DIR="$(cd "$HERE/../../../.." && pwd 2>/dev/null || true)"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
APP_NAME="${ROTO_APP_NAME:-}"
if [[ -z "$APP_NAME" && -n "$BUNDLE_DIR" && "$BUNDLE_DIR" == *.app ]]; then
  APP_NAME="$(basename "$BUNDLE_DIR" .app)"
fi
APP_NAME="${APP_NAME:-Light-PDF}"
APP_SUPPORT_DIR="${ROTO_APP_SUPPORT_DIR:-$HOME/Library/Application Support/$APP_NAME}"
SITE_PACKAGES="$APP_SUPPORT_DIR/site-packages"
# Résolution robuste du chemin vers streamlit_app.py (bundle ou source)
if [[ -f "$HERE/../streamlit_app.py" ]]; then
  APP_CODE="$HERE/../streamlit_app.py"
elif [[ -f "$HERE/../LightPDF/streamlit_app.py" ]]; then
  APP_CODE="$HERE/../LightPDF/streamlit_app.py"
else
  APP_CODE=""
fi
DEFAULT_PORT="${PORT:-8501}"
PORT="$(python3 - <<'PY'
import os, socket
start = int(os.environ.get("PORT", "8501"))
def free(p: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", p))
        except OSError:
            return False
    return True
chosen = None
for port in range(start, start + 50):
    if free(port):
        chosen = port
        break
print(chosen or start)
PY
)"
if [[ "$PORT" != "$DEFAULT_PORT" ]]; then
  echo "[Light-PDF] Port $DEFAULT_PORT indisponible, utilisation du port $PORT."
fi
HEADLESS="${HEADLESS:-false}"

mkdir -p "$SITE_PACKAGES"

PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "[Light-PDF] Erreur: python3 introuvable." >&2
    exit 1
  fi
fi

export PYTHONPATH="$SITE_PACKAGES${PYTHONPATH:+:$PYTHONPATH}"
export ROTO_APP_NAME="$APP_NAME"
export ROTO_APP_SUPPORT_DIR="$APP_SUPPORT_DIR"

if [[ -z "$APP_CODE" || ! -f "$APP_CODE" ]]; then
  echo "[Light-PDF] Erreur: streamlit_app.py introuvable (chemins testés depuis $HERE)." >&2
  exit 1
fi

ensure_deps() {
  local missing
  missing="$("$PYTHON_BIN" - <<'PY'
import importlib.util, json
need = ["streamlit", "PyPDF2", "pdf2image", "reportlab", "Pillow"]
missing = [m for m in need if importlib.util.find_spec(m) is None]
print(json.dumps(missing))
PY
)"
  if [[ "$missing" != "[]" ]]; then
    echo "[Light-PDF] Installation des dépendances dans '$SITE_PACKAGES'..."
    "$PYTHON_BIN" -m pip install --upgrade --no-warn-script-location --target "$SITE_PACKAGES" streamlit PyPDF2 pdf2image reportlab pillow >/tmp/lightpdf_install.log 2>&1 || {
      echo "[Light-PDF] Échec de l'installation des dépendances. Voir /tmp/lightpdf_install.log" >&2
      exit 1
    }
  fi
}

ensure_deps

if ! command -v gs >/dev/null 2>&1; then
  echo "[Light-PDF] Info: Ghostscript (gs) non détecté. Requis uniquement pour l'option vectorielle (aplat transparences)." >&2
fi

export BROWSER="${BROWSER:-open}"

exec "$PYTHON_BIN" -m streamlit run "$APP_CODE" \
  --server.headless "$HEADLESS" \
  --server.port "$PORT" \
  --server.address "127.0.0.1" \
  "$@"
