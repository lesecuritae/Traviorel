from __future__ import annotations
import os
from pathlib import Path
def read_internal_token() -> str:
    explicit = os.environ.get("DB_CFFI_TOKEN", "")
    if explicit:
        if explicit != explicit.strip() or any(c.isspace() for c in explicit): raise RuntimeError("DB_CFFI_TOKEN is invalid")
        return explicit
    filename = os.environ.get("DB_CFFI_TOKEN_FILE", "").strip()
    if not filename: raise RuntimeError("DB_CFFI_TOKEN or DB_CFFI_TOKEN_FILE is required")
    try: token = Path(filename).read_text(encoding="utf-8").strip()
    except OSError as error: raise RuntimeError("DB_CFFI_TOKEN_FILE cannot be read") from error
    if len(token) != 64 or any(c not in "0123456789abcdefABCDEF" for c in token): raise RuntimeError("DB_CFFI_TOKEN_FILE contains an invalid token")
    return token
