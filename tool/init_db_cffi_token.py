from __future__ import annotations
import os, secrets
from pathlib import Path
TOKEN_FILE = Path(os.environ.get("DB_CFFI_TOKEN_FILE", "/run/traviorel-secrets/db_cffi_token"))
def write_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(TOKEN_FILE.parent, 0o755)
    if os.geteuid() == 0: os.chown(TOKEN_FILE.parent, 0, 10001)
    temporary = TOKEN_FILE.with_name(f".{TOKEN_FILE.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n"); handle.flush(); os.fsync(handle.fileno())
        if os.geteuid() == 0: os.chown(temporary, 0, 10001)
        os.chmod(temporary, 0o444); os.replace(temporary, TOKEN_FILE)
    finally: temporary.unlink(missing_ok=True)
def main() -> None:
    TOKEN_FILE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(TOKEN_FILE.parent, 0o755)
    if os.geteuid() == 0: os.chown(TOKEN_FILE.parent, 0, 10001)
    explicit = os.environ.get("DB_CFFI_TOKEN", "")
    if explicit:
        if explicit != explicit.strip() or any(c.isspace() for c in explicit): raise SystemExit("DB_CFFI_TOKEN is invalid")
        write_token(explicit); return
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) == 64 and all(c in "0123456789abcdefABCDEF" for c in token):
            os.chmod(TOKEN_FILE, 0o444)
            return
        raise SystemExit("Persisted DB_CFFI_TOKEN is invalid")
    write_token(secrets.token_hex(32))
if __name__ == "__main__": main()
