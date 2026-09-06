from pathlib import Path
import hashlib
import os

root = Path(os.environ.get("SOURCE_ROOT", Path(__file__).resolve().parents[1]))
compose = (root / "compose.yml").read_text(encoding="utf-8")
env_example = (root / ".env.example").read_text(encoding="utf-8")
readme_de = (root / "README.de.md").read_text(encoding="utf-8")
readme_en = (root / "README.en.md").read_text(encoding="utf-8")
third_party = (root / "THIRD_PARTY.md").read_text(encoding="utf-8")
check = (root / "scripts" / "check.sh").read_text(encoding="utf-8")
container_check = (root / "scripts" / "container-check.sh").read_text(encoding="utf-8")
app_dockerfile = (root / "tool" / "Dockerfile").read_text(encoding="utf-8")
db_api_dockerfile = (root / "db-api" / "Dockerfile").read_text(encoding="utf-8")
db_api_server = (root / "db-api" / "server.mjs").read_text(encoding="utf-8")
pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
runtime_requirements = (root / "tool" / "requirements.txt").read_text(encoding="utf-8")
installer = root / "install.sh"
installer_text = installer.read_text(encoding="utf-8")
license_file = root / "LICENSE"

assert installer.is_file(), "install.sh fehlt"
assert "docker compose pull" in installer_text
assert "docker compose up -d --no-build --remove-orphans" in installer_text
assert "docker compose build" not in installer_text
assert "interne Bridge-Token verwaltet Compose automatisch" in installer_text
assert license_file.is_file(), "LICENSE fehlt"
assert "MIT License" in license_file.read_text(encoding="utf-8")
assert '${TRAVIOREL_BIND_HOST:-127.0.0.1}:${TRAVIOREL_PORT:-8791}:8000' in compose
assert '0.0.0.0:8791:8000' not in compose
assert 'traviorel-state:/var/lib/reisevergleich' in compose
assert "${DB_CFFI_TOKEN:-}" in compose
assert "DB_CFFI_TOKEN_FILE: /run/traviorel-secrets/db_cffi_token" in compose
assert "traviorel-secrets:/run/traviorel-secrets:ro" in compose
assert "DB_CFFI_TOKEN=\n" in env_example
assert "ghcr.io/lesecuritae/traviorel-app:latest" in compose
assert "ghcr.io/lesecuritae/traviorel-db-api:latest" in compose
assert "condition: service_healthy" in compose
assert 'HISTORY_SNAPSHOT_SCHEDULER_ENABLED: "${HISTORY_SNAPSHOT_SCHEDULER_ENABLED:-true}"' in compose
assert 'HISTORY_SNAPSHOT_INTERVAL_SECONDS: "${HISTORY_SNAPSHOT_INTERVAL_SECONDS:-86400}"' in compose
assert 'SEARCH_DEPARTURE_TOLERANCE_MINUTES: "${SEARCH_DEPARTURE_TOLERANCE_MINUTES:-15}"' in compose
assert "HEALTHCHECK" in app_dockerfile and "/api/health" in app_dockerfile
assert "HEALTHCHECK" in db_api_dockerfile and "/health" in db_api_dockerfile
assert "traviorel/0.3.0" in db_api_server and "traviorel/0.0.6" not in db_api_server
assert 'version = "0.3.0"' in pyproject
assert "Pillow" not in runtime_requirements
for coverage_file in ("provider.py", "mapper.py", "analyzer.py", "cache.py"):
    assert (root / "tool" / "reisevergleich" / "coverage" / coverage_file).is_file()
assert (root / "tool" / "reisevergleich" / "coverage" / "opencellid.py").is_file()
assert "OPENCELLID_API_KEY" in compose and "OPENCELLID_API_KEY=" in env_example
assert "OpenCellID" in third_party
coverage_data = root / "tool" / "reisevergleich" / "coverage" / "data" / "mobile_broadband_2025_12.fwcov"
assert coverage_data.stat().st_size > 1_000_000
assert hashlib.sha256(coverage_data.read_bytes()).hexdigest() == "25330fedbd820a4ee6064aaeb2c4dabf621f10ab6b9df4feaad5c0a8e978b0c6"
assert 'name = "traviorel"' in pyproject and 'testpaths = ["tests"]' in pyproject
test_requirements = (root / "tool" / "requirements-test.txt").read_text(encoding="utf-8")
assert "-r requirements.txt" in test_requirements
assert "pytest>=8,<9" in test_requirements and "pytest-asyncio" in test_requirements
assert "## Installation mit Docker" in readme_de
assert "## Installation with Docker" in readme_en
assert "docker compose config -q" in readme_de and "docker compose config -q" in readme_en
assert "docker compose up -d --no-build" in readme_de and "docker compose up -d --no-build" in readme_en
assert "PolyForm Noncommercial License 1.0.0" in third_party
assert "Required Notice" in third_party
assert "ISC License" in third_party
assert "Bundesnetzagentur Mobilfunk-Monitoring" in third_party
assert "Mobilfunkabdeckung entlang der Fahrt" in readme_de
assert "Mobile coverage along a journey" in readme_en
assert "curl_cffi" in check.split('if python3 -c', 1)[1].split('then', 1)[0]
assert '-v "$ROOT:/source:ro"' in container_check
assert 'export DB_CFFI_TOKEN="${DB_CFFI_TOKEN:-' in container_check, (
    "container-check.sh muss aus einem frischen Archiv ohne .env lauffaehig sein"
)

for forbidden in ("192.168.", "10.0.", "tailscale", "CHANGE_ME="):
    assert forbidden not in compose

print("GitHub-Paketvertrag, sichere Defaults und Lizenzhinweise: OK")
