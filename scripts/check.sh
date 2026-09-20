#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
trap 'printf "\nTEST ABGEBROCHEN\n" >&2; exit 130' INT TERM

say(){ printf '\n=== %s ===\n' "$1"; }
die(){ echo "FEHLER: $*" >&2; exit 1; }

say "Python- und JavaScript-Syntax"
export PYTHONPATH="$ROOT/tool${PYTHONPATH:+:$PYTHONPATH}"
python3 -m compileall -q tool tests
if command -v node >/dev/null 2>&1; then
  node --check tool/ui/app.js
fi

echo "Syntax: OK"

say "Quellsauberkeit"
python3 tests/test_source_clean.py

say "Logik- und Vertragsprüfungen"
if python3 -c "import fastapi, httpx, pydantic, uvicorn, curl_cffi" 2>/dev/null; then
  # Kein Index-Aufbau aus dem Netz und keine Preise im Datenordner; die Tests dafür schalten beides selbst ein.
  export DELAY_INDEX=0 PRICE_HISTORY=0
  for test in tests/test_*.py; do
    echo "--- $(basename "$test")"
    python3 "$test"
  done
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Host-Abhängigkeiten fehlen; Prüfungen laufen im gebauten App-Container."
  bash scripts/container-check.sh
else
  die "Host-Abhängigkeiten fehlen und Docker Compose ist nicht verfügbar. Installiere tool/requirements.txt oder nutze eine Docker-Umgebung."
fi

find tool tests -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find tool tests -name '*.py[co]' -delete 2>/dev/null || true

echo
printf '%s\n' 'CHECK: OK'
