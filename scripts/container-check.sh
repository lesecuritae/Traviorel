#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# Compose verlangt den internen Bridge-Token bereits beim Parsen. Die isolierten
# Regressionen starten kein DB-Backend und brauchen deshalb nur einen Testwert.
export DB_CFFI_TOKEN="${DB_CFFI_TOKEN:-0000000000000000000000000000000000000000000000000000000000000000}"

trap 'printf "\nCONTAINER-TEST ABGEBROCHEN\n" >&2; exit 130' INT TERM

docker compose run --rm --no-deps \
  -v "$ROOT:/source:ro" \
  -e SOURCE_ROOT=/source \
  app sh -lc '
    set -eu
    export PYTHONPATH=/source/tool
    # Viele Tests sind pytest-Funktionen und laufen als Skript gar nicht; andere fuehren ihre
    # Pruefungen als Top-Level-Asserts aus und laufen auch ohne pytest. Die pytest-Verfuegbarkeit
    # muss VOR der Dateischleife geprueft werden: ein reines Skript-"python $f" auf einer
    # pytest-Testdatei bricht sonst mit ModuleNotFoundError ab, bevor der Hinweis unten je laeuft.
    if python -c "import pytest" 2>/dev/null; then
      cd /source && python -m pytest -q -p no:cacheprovider
    else
      echo "HINWEIS: pytest fehlt im Image; pytest-basierte Tests werden NICHT ausgefuehrt (CI: python -m pytest -q)."
      for f in /source/tests/test_*.py; do
        if grep -ql "^import pytest\|^from pytest\b" "$f"; then
          echo "--- $(basename "$f") uebersprungen (benoetigt pytest)"
          continue
        fi
        echo "--- $(basename "$f")"
        python "$f"
      done
    fi
    python - <<"PY"
import reisevergleich.api, reisevergleich.service, reisevergleich.presentation
print("Produktionsimporte: OK")
PY
  '

echo
printf '%s\n' 'CONTAINER-CHECK: OK'
