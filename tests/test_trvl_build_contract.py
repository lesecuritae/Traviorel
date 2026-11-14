from pathlib import Path
import os

root = Path(os.environ.get("SOURCE_ROOT", Path(__file__).resolve().parents[1]))
dockerfile = (root / "tool" / "Dockerfile").read_text()
compose = (root / "compose.yml").read_text()
third_party = (root / "THIRD_PARTY.md").read_text()

assert "ARG TRVL_REF=b20e2265a63a4d229abff7b20f5649f2e9f948dc" in dockerfile
assert "ARG TRVL_VERSION=1.27.0+git.b20e226" in dockerfile
assert "main.Version=${TRVL_VERSION}" in dockerfile
assert 'grep -F "trvl ${TRVL_VERSION}"' in dockerfile
assert "main.Version=1.24.0" not in dockerfile
for contract in ("trvl hotels --help", '"--enrich-rooms"', '"--checkin"', "trvl flights --help", '"--return"', "trvl ground --help", "trvl airport-transfer --help", '"--arrival-after"', "trvl route --help", '"--arrive-by"'):
    assert contract in dockerfile
assert "TRVL_REF: ${TRVL_REF:-b20e2265a63a4d229abff7b20f5649f2e9f948dc}" in compose
assert "geprüfter Upstream-Commit `b20e2265a63a4d229abff7b20f5649f2e9f948dc`" in third_party

print("trvl-Version ist einstellbar und CLI-Verträge werden beim Build geprüft: OK")

# Supply chain: the mutable tag must resolve to the reviewed commit or the build fails.
assert "ARG TRVL_COMMIT=b20e2265a63a4d229abff7b20f5649f2e9f948dc" in dockerfile
assert 'if [ "$actual" != "$TRVL_COMMIT" ]' in dockerfile
assert compose.count("TRVL_COMMIT: ${TRVL_COMMIT:-b20e2265a63a4d229abff7b20f5649f2e9f948dc}") == compose.count("TRVL_REF: ${TRVL_REF:-b20e2265a63a4d229abff7b20f5649f2e9f948dc}")

assert "golang:1.27.3-bookworm@sha256:" in dockerfile
assert 'git checkout --detach FETCH_HEAD' in dockerfile
assert "GOTOOLCHAIN=local" in dockerfile
assert "RUN --network=none" in dockerfile
assert "source=trvl-test-hosts,target=/etc/hosts" in dockerfile
assert "93.184.216.34 example.com" in (root / "tool" / "trvl-test-hosts").read_text()
for package in ("./internal/flights/afklm", "./internal/flights", "./internal/hotels", "./internal/providers", "./internal/ground"):
    assert package in dockerfile
assert compose.count("TRVL_VERSION: ${TRVL_VERSION:-1.27.0+git.b20e226}") == 2
