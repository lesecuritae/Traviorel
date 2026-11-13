"""Offline contracts for the DB transport; live availability is tested separately."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("DB_CFFI_TOKEN", "unit-test-dummy-token")
import db_cffi_bridge as bridge


def test_default_is_supported_firefox155(monkeypatch):
    monkeypatch.delenv("DB_CFFI_IMPERSONATE_POOL", raising=False)
    assert bridge._profile_pool() == ("firefox155",)
    assert "firefox155" in bridge._supported_browser_profiles()


def test_operator_override_keeps_supported_profiles(monkeypatch):
    monkeypatch.setenv("DB_CFFI_IMPERSONATE_POOL", "firefox154,firefox155,firefox154,invalid")
    assert bridge._profile_pool() == ("firefox154", "firefox155")


def test_invalid_override_returns_verified_default(monkeypatch):
    monkeypatch.setenv("DB_CFFI_IMPERSONATE_POOL", "invalid")
    assert bridge._profile_pool() == ("firefox155",)


@pytest.mark.parametrize("path", ["/mob/location/search", "/mob/angebote/fahrplan", "/mob/angebote/recon"])
def test_browser_update_preserves_authenticated_transport(monkeypatch, path):
    calls = []
    response = SimpleNamespace(status_code=200, url="https://app.services-bahn.de" + path,
                               http_version=3, headers={"content-type": "application/json"}, text='{"ok":true}')
    class Session:
        def __init__(self, **kwargs):
            assert kwargs["impersonate"] == "firefox155"
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return response
        def close(self):
            pass
    monkeypatch.setattr(bridge.requests, "Session", Session)
    monkeypatch.setattr(bridge, "_FIREFOX_POOL", ("firefox155",))
    monkeypatch.setattr(bridge, "_sessions", {})
    payload = bridge.DbCffiRequest(method="post", url=response.url,
        headers={"Content-Type": "application/json", "User-Agent": "discarded"},
        body={"query": "fixture"}, session_key="offline-fixture")
    first = bridge.db_cffi_request(payload, bridge._TOKEN)
    second = bridge.db_cffi_request(payload, bridge._TOKEN)
    assert first["fingerprint_profile"] == "firefox155"
    assert first["status"] == second["status"] == 200
    assert first["session_reused"] is False and second["session_reused"] is True
    assert len(calls) == 2
    assert calls[0][0] == "POST"
    assert calls[0][2]["data"] == b'{"query":"fixture"}'
    assert "User-Agent" not in calls[0][2]["headers"]
    assert calls[0][2]["allow_redirects"] is False


def test_dependency_pin_includes_new_profile():
    requirements = (Path(__file__).resolve().parents[1] / "tool/requirements.txt").read_text()
    assert "curl_cffi==0.17.0" in requirements.splitlines()


def test_upstream_block_is_reported_without_replay(monkeypatch):
    calls = []
    response = SimpleNamespace(status_code=452, url="https://app.services-bahn.de/mob/angebote/fahrplan",
                               http_version=3, headers={}, text='{"code":"OPS_BLOCKED"}')
    session = SimpleNamespace(request=lambda *args, **kwargs: (calls.append(args), response)[1])
    entry = SimpleNamespace(session=session, profile="firefox155")
    monkeypatch.setattr(bridge, "_session_entry", lambda key: ("fixture", entry, False))
    result = bridge.db_cffi_request(bridge.DbCffiRequest(url=response.url), bridge._TOKEN)
    assert result["status"] == 452
    assert result["body"] == response.text
    assert result["fingerprint_rotated"] is False
    assert len(calls) == 1
