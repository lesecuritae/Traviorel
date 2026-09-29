from pathlib import Path
import os

root = Path(os.environ.get("SOURCE_ROOT", Path(__file__).resolve().parents[1]))
dockerfile = (root / "tool" / "Dockerfile").read_text()
compose = (root / "compose.yml").read_text()
third_party = (root / "THIRD_PARTY.md").read_text()

assert "ARG TRVL_REF=v1.21.4" in dockerfile
assert 'TRVL_VERSION="${TRVL_REF#v}"' in dockerfile
assert "main.Version=${TRVL_VERSION}" in dockerfile
assert 'grep -F "trvl ${TRVL_VERSION}"' in dockerfile
assert "main.Version=1.21.4" not in dockerfile
for contract in ("trvl hotels --help", '"--enrich-rooms"', "trvl flights --help", "trvl ground --help"):
    assert contract in dockerfile
assert "TRVL_REF: ${TRVL_REF:-v1.21.4}" in compose
assert "Tag `v1.21.4`" in third_party

print("trvl-Version ist einstellbar und CLI-Verträge werden beim Build geprüft: OK")

# Supply chain: the mutable tag must resolve to the reviewed commit or the build fails.
assert "ARG TRVL_COMMIT=0d5bbc4947f102de12e7a277cec3c714c40e1656" in dockerfile
assert 'if [ "$actual" != "$TRVL_COMMIT" ]' in dockerfile
assert compose.count("TRVL_COMMIT: ${TRVL_COMMIT:-0d5bbc4947f102de12e7a277cec3c714c40e1656}") == compose.count("TRVL_REF: ${TRVL_REF:-v1.21.4}")
