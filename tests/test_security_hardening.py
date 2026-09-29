"""Security hardening from the 2026-09-28 audit (FareWeave run-1 leads 4, 8, 9, 10, 11)."""
import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

os.environ.setdefault("DB_CFFI_TOKEN", "unit-test-dummy-token")  # the bridge reads it at import
import db_cffi_bridge  # noqa: E402
from reisevergleich import gtfs_flix
from reisevergleich.api import router
from reisevergleich.keyed_locks import KeyedAsyncLocks, KeyedThreadLocks
from reisevergleich.models import PLACE_MAX_LENGTH, HotelRequest, ReiseRequest, TripRequest
from reisevergleich.trvl import _place_arg

BASE = dict(origin="Startort", destination="Zielort", departure_date="2030-09-15")


# Lead 11: list validators were quadratic in the caller-supplied list length.

def test_oversized_split_candidate_list_is_rejected_before_the_validator():
    started = time.monotonic()
    with pytest.raises(ValidationError) as err:
        TripRequest(**BASE, feeder_split_candidates=[f"s{i}" for i in range(50_000)])
    assert time.monotonic() - started < 1.0
    assert any(e["type"] == "too_long" for e in err.value.errors())


def test_oversized_origin_airport_list_is_rejected():
    with pytest.raises(ValidationError):
        TripRequest(**BASE, origin_airports=["FRA"] * 13)


def test_split_candidates_still_dedupe_casefolded_and_keep_eight():
    req = TripRequest(**BASE, feeder_split_candidates=["Köln", "köln", " Bonn ", ""] + [f"S{i}" for i in range(20)])
    assert req.feeder_split_candidates == ["Köln", "Bonn", "S0", "S1", "S2", "S3", "S4", "S5"]


def test_origin_airports_still_normalise_dedupe_and_keep_six():
    req = TripRequest(**BASE, origin_airports=["fra", "FRA", "muc", "ber", "ham", "cgn", "dus", "stR"])
    assert req.origin_airports == ["FRA", "MUC", "BER", "HAM", "CGN", "DUS"]


# Lead 10: place names had no length bound.

def test_place_names_are_bounded():
    TripRequest(**{**BASE, "origin": "x" * PLACE_MAX_LENGTH})
    with pytest.raises(ValidationError):
        TripRequest(**{**BASE, "origin": "x" * (PLACE_MAX_LENGTH + 1)})
    with pytest.raises(ValidationError):
        ReiseRequest(origin="a", destination="b" * 5000, travel_date="2030-09-15")
    with pytest.raises(ValidationError):
        HotelRequest(location="h" * 5000, checkin_date="2030-09-15", checkout_date="2030-09-16")


def test_flix_stops_query_is_bounded():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.get("/api/flix-stops", params={"origin": "x" * 5000, "destination": "Berlin"})
    assert response.status_code == 422


def _reference_stop_score(query, name):
    # The pre-change algorithm, kept here to prove the hoisted scorer returns the same values.
    k, pt, exact, air = gtfs_flix._key, gtfs_flix._place_tokens, gtfs_flix.exact_location_key, gtfs_flix.has_airport_context
    if exact(query) == exact(name):
        return 110
    qk, nk = k(query), k(name)
    if not qk or not nk:
        return 0
    pen = 20 if not air(query) and air(name) else 0
    if qk == nk:
        return 100 - pen
    qt, nt = pt(query), pt(name)
    if qt and qt == nt:
        return 90 - pen
    if nk.startswith(qk + " "):
        return 80 - pen
    if qt and qt <= nt:
        return 75 - pen
    if qk in nk:
        return 60 - pen
    return 0


@pytest.mark.parametrize("query", ["Berlin", "berlin hbf", "München Flughafen", "Frankfurt (Main)", "Köln", "", "  ", "ZOB"])
def test_hoisted_stop_scorer_matches_the_original(query):
    names = ["Berlin Hbf", "Berlin ZOB", "Berlin Airport BER", "München Hbf", "Munich Airport", "Frankfurt (Main) Hbf",
             "Köln Hbf", "Cologne Airport", "Hamburg ZOB", "Stop"]
    score = gtfs_flix.make_stop_scorer(query)
    for name in names:
        assert score(name) == _reference_stop_score(query, name) == gtfs_flix.stop_score(query, name)


# Lead 4: place names reach trvl argv as positionals.

@pytest.mark.parametrize("value", ["-h", "--help", "--provider=x"])
def test_option_like_place_names_are_rejected(value):
    with pytest.raises(ValidationError):
        TripRequest(**{**BASE, "origin": value})
    with pytest.raises(ValidationError):
        ReiseRequest(origin=value, destination="b", travel_date="2030-09-15")
    with pytest.raises(ValidationError):
        HotelRequest(location=value, checkin_date="2030-09-15", checkout_date="2030-09-16")
    with pytest.raises(ValueError):
        _place_arg(value)


def test_ordinary_place_names_pass_through():
    assert _place_arg("Baden-Baden") == "Baden-Baden"
    assert ReiseRequest(origin="Baden-Baden", destination="b", travel_date="2030-09-15").origin == "Baden-Baden"


# Lead 9: per-key lock maps grew by one entry per distinct key, forever.

async def test_async_keyed_locks_are_released_and_exclusive():
    locks = KeyedAsyncLocks()
    for i in range(500):
        async with locks.hold(f"k{i}"):
            pass
    assert len(locks) == 0

    inside, peak = 0, 0

    async def worker():
        nonlocal inside, peak
        async with locks.hold("same"):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.001)
            inside -= 1

    await asyncio.gather(*(worker() for _ in range(20)))
    assert peak == 1
    assert len(locks) == 0


async def test_async_keyed_lock_is_released_when_the_body_raises():
    locks = KeyedAsyncLocks()
    with pytest.raises(RuntimeError):
        async with locks.hold("k"):
            raise RuntimeError("boom")
    assert len(locks) == 0


def test_thread_keyed_locks_are_released_and_exclusive():
    locks = KeyedThreadLocks()
    inside, peak = [0], [0]
    guard = threading.Lock()

    def worker():
        with locks.hold("same"):
            with guard:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.001)
            with guard:
                inside[0] -= 1

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1
    for i in range(200):
        with locks.hold(f"k{i}"):
            pass
    assert len(locks) == 0


# Lead 8: the bridge followed redirects past its host allowlist.

class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("data"), kwargs.get("allow_redirects")))
        status, location = self.responses.pop(0)
        headers = {"location": location} if location else {}
        return SimpleNamespace(status_code=status, headers=headers, url=url, http_version=2, text="ok")


def _entry(session):
    return SimpleNamespace(session=session, profile="firefox", created_at=0, last_used=0)


def test_redirect_off_the_allowlist_is_refused():
    session = _FakeSession([(302, "https://evil.example/steal")])
    with pytest.raises(db_cffi_bridge.RedirectRejected):
        db_cffi_bridge._request_with_entry(_entry(session), "GET", "https://www.bahn.de/x", {}, None)
    assert len(session.calls) == 1 and session.calls[0][3] is False


def test_redirect_downgrade_to_http_is_refused():
    session = _FakeSession([(301, "http://www.bahn.de/x")])
    with pytest.raises(db_cffi_bridge.RedirectRejected):
        db_cffi_bridge._request_with_entry(_entry(session), "GET", "https://www.bahn.de/x", {}, None)


def test_allowed_redirect_is_followed_and_303_becomes_get():
    session = _FakeSession([(303, "/next"), (302, "https://int.bahn.de/final"), (200, None)])
    response = db_cffi_bridge._request_with_entry(_entry(session), "POST", "https://www.bahn.de/start", {}, '{"a":1}')
    assert response.status_code == 200
    assert [c[:3] for c in session.calls] == [
        ("POST", "https://www.bahn.de/start", b'{"a":1}'),
        ("GET", "https://www.bahn.de/next", None),
        ("GET", "https://int.bahn.de/final", None),
    ]


def test_307_keeps_method_and_body():
    session = _FakeSession([(307, "https://www.bahn.de/again"), (200, None)])
    db_cffi_bridge._request_with_entry(_entry(session), "POST", "https://www.bahn.de/start", {}, "b")
    assert session.calls[1][:3] == ("POST", "https://www.bahn.de/again", b"b")


def test_redirect_loop_is_bounded():
    session = _FakeSession([(302, "https://www.bahn.de/loop")] * 10)
    with pytest.raises(db_cffi_bridge.RedirectRejected):
        db_cffi_bridge._request_with_entry(_entry(session), "GET", "https://www.bahn.de/loop", {}, None)
    assert len(session.calls) == db_cffi_bridge._MAX_REDIRECTS + 1


# Lead 1 (app side): split candidates multiply db-api searches per request.

async def test_ground_mixed_handoffs_use_the_feeder_clamp(monkeypatch):
    from reisevergleich import ground_mixed

    seen = []

    async def fake_via(origin, destination, handoff, *args, **kwargs):
        seen.append(handoff)
        return []

    async def fake_flix(*args, **kwargs):
        return []

    monkeypatch.setattr(ground_mixed, "_outbound_via_options", fake_via)
    monkeypatch.setattr(ground_mixed, "_return_via_options", fake_via)
    monkeypatch.setattr(ground_mixed, "_flix_three_part", fake_flix)
    await ground_mixed.ground_mixed_options(
        "A", "B", "2030-09-15", "06:00", [f"S{i}" for i in range(8)], include_flixbus=True, include_flixtrain=True,
    )
    assert sorted(set(seen)) == [f"S{i}" for i in range(ground_mixed.MAX_FEEDER_HANDOFFS)]
    assert len(seen) == 2 * ground_mixed.MAX_FEEDER_HANDOFFS
