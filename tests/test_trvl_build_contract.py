from pathlib import Path
import os

root = Path(os.environ.get("SOURCE_ROOT", Path(__file__).resolve().parents[1]))
dockerfile = (root / "tool" / "Dockerfile").read_text()
compose = (root / "compose.yml").read_text()
third_party = (root / "THIRD_PARTY.md").read_text()

assert "ARG TRVL_REF=v1.24.0" in dockerfile
assert 'TRVL_VERSION="${TRVL_REF#v}"' in dockerfile
assert "main.Version=${TRVL_VERSION}" in dockerfile
assert 'grep -F "trvl ${TRVL_VERSION}"' in dockerfile
assert "main.Version=1.24.0" not in dockerfile
for contract in ("trvl hotels --help", '"--enrich-rooms"', '"--checkin"', "trvl flights --help", '"--return"', "trvl ground --help", "trvl airport-transfer --help", '"--arrival-after"', "trvl route --help", '"--arrive-by"'):
    assert contract in dockerfile
assert "TRVL_REF: ${TRVL_REF:-v1.24.0}" in compose
assert "Tag `v1.24.0`" in third_party

print("trvl-Version ist einstellbar und CLI-Verträge werden beim Build geprüft: OK")

# Supply chain: the mutable tag must resolve to the reviewed commit or the build fails.
assert "ARG TRVL_COMMIT=4f2b0d099e51931584826d4feecb14a2cfab5f66" in dockerfile
assert 'if [ "$actual" != "$TRVL_COMMIT" ]' in dockerfile
assert compose.count("TRVL_COMMIT: ${TRVL_COMMIT:-4f2b0d099e51931584826d4feecb14a2cfab5f66}") == compose.count("TRVL_REF: ${TRVL_REF:-v1.24.0}")
