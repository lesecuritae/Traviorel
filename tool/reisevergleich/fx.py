"""Wechselkurse der Europäischen Zentralbank (öffentlich, ohne Schlüssel).

Manche Quellen liefern Preise in Dollar (Skiplagged zum Beispiel). Damit sie neben Euro-Preisen verglichen werden
können, rechnet Traviorel sie mit dem Tageskurs der EZB um. Ohne Kurs bleibt der Preis unverändert und wird nicht
als Euro ausgegeben.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import urllib.request

_URL = os.environ.get("ECB_RATES_URL", "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml")
_TTL = 6 * 3600
_FAILURE_TTL = 15 * 60
_rates: dict[str, float] = {}
_loaded_at = 0.0
_loaded_ok = False


def parse_rates(xml: str) -> dict[str, float]:
    return {code: float(rate) for code, rate in re.findall(r"currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9.]+)['\"]", xml)}


def _load_sync() -> dict[str, float]:
    request = urllib.request.Request(_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; Traviorel)"})
    with urllib.request.urlopen(request, timeout=8) as response:  # nosec B310 - feste https-Adresse
        return parse_rates(response.read(200_000).decode("utf-8", errors="replace"))


async def ensure_rates() -> None:
    global _rates, _loaded_at, _loaded_ok
    ttl = _TTL if _loaded_ok else _FAILURE_TTL
    if _loaded_at and time.monotonic() - _loaded_at < ttl:
        return
    _loaded_at = time.monotonic()
    try:
        rates = await asyncio.to_thread(_load_sync)
    except Exception:  # noqa: BLE001 - ohne Kurs bleibt alles wie zuvor
        _loaded_ok = False
        return
    if rates:
        _rates, _loaded_ok = rates, True


def to_eur(amount: float, currency: str | None) -> float | None:
    """Betrag in Euro; ``None``, wenn die Währung unbekannt ist oder kein Kurs vorliegt."""
    code = str(currency or "EUR").upper()
    if code == "EUR":
        return float(amount)
    rate = _rates.get(code)
    return round(float(amount) / rate, 2) if rate else None
