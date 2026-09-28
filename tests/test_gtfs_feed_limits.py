"""Flix GTFS download and expansion limits, and refresh backoff (audit 2026-09-28, leads 3 and 5)."""
import zipfile

import httpx
import pytest

from reisevergleich import gtfs_flix


@pytest.fixture
def feed_env(tmp_path, monkeypatch):
    calls = []
    state = {"handler": None}

    def transport_handler(request):
        calls.append(request)
        return state["handler"](request)

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(transport_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gtfs_flix.httpx, "Client", client_factory)
    monkeypatch.setattr(gtfs_flix, "FLIX_GTFS_DIR", str(tmp_path))
    monkeypatch.setattr(gtfs_flix, "_refresh_failures", 0)
    monkeypatch.setattr(gtfs_flix, "_refresh_retry_at", 0.0)
    return tmp_path, calls, state


def test_download_over_the_byte_cap_is_refused_and_not_kept(feed_env, monkeypatch):
    directory, calls, state = feed_env
    monkeypatch.setattr(gtfs_flix, "FLIX_GTFS_MAX_DOWNLOAD_BYTES", 10_000)
    state["handler"] = lambda request: httpx.Response(200, content=b"x" * 50_000)
    with pytest.raises(ValueError, match="zu groß"):
        gtfs_flix._refresh_sync()
    assert not (directory / "flix.sqlite3").exists()
    assert list(directory.iterdir()) == [], "the partial download must be cleaned up"


def test_declared_content_length_over_the_cap_is_refused_before_reading(feed_env, monkeypatch):
    _, _, state = feed_env
    monkeypatch.setattr(gtfs_flix, "FLIX_GTFS_MAX_DOWNLOAD_BYTES", 10_000)
    state["handler"] = lambda request: httpx.Response(200, headers={"content-length": "999999999"}, content=b"")
    with pytest.raises(ValueError, match="zu groß"):
        gtfs_flix._refresh_sync()


def test_download_past_the_overall_deadline_is_refused(feed_env, monkeypatch):
    _, _, state = feed_env
    monkeypatch.setattr(gtfs_flix, "FLIX_GTFS_MAX_DOWNLOAD_SECONDS", -1)
    state["handler"] = lambda request: httpx.Response(200, content=b"x" * 5000)
    with pytest.raises(TimeoutError):
        gtfs_flix._refresh_sync()


def test_failed_refresh_backs_off_instead_of_retrying_per_request(feed_env):
    directory, calls, state = feed_env
    state["handler"] = lambda request: httpx.Response(503)
    with pytest.raises(httpx.HTTPStatusError):
        gtfs_flix._refresh_sync()
    for _ in range(5):
        with pytest.raises(gtfs_flix.FeedRefreshBackoff):
            gtfs_flix._refresh_sync()
    assert len(calls) == 1, "callers during the backoff must not hit the upstream again"


def test_backoff_serves_the_last_valid_feed(feed_env, monkeypatch):
    directory, calls, state = feed_env
    database = directory / "flix.sqlite3"
    database.write_bytes(b"old")
    monkeypatch.setattr(gtfs_flix, "_database_current", lambda _db: False)
    state["handler"] = lambda request: httpx.Response(503)
    assert gtfs_flix._refresh_sync() == database
    assert gtfs_flix._refresh_sync() == database
    assert len(calls) == 1
    assert gtfs_flix._refresh_retry_at > 0


def test_backoff_doubles_up_to_the_cap(feed_env, monkeypatch):
    _, _, state = feed_env
    state["handler"] = lambda request: httpx.Response(503)
    now = [1_000_000.0]
    monkeypatch.setattr(gtfs_flix.time, "time", lambda: now[0])
    waits = []
    for _ in range(6):
        with pytest.raises(httpx.HTTPStatusError):
            gtfs_flix._refresh_sync()
        waits.append(gtfs_flix._refresh_retry_at - now[0])
        now[0] = gtfs_flix._refresh_retry_at + 1
    assert waits == [300, 600, 1200, 2400, 3600, 3600]


def _zip(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_highly_compressed_member_is_refused(tmp_path):
    archive_path = tmp_path / "bomb.zip"
    _zip(archive_path, {"stop_times.txt": b"\0" * (20 * 1024 * 1024)})
    with pytest.raises(ValueError, match="komprimiert"):
        gtfs_flix._build_database(archive_path, tmp_path / "out.sqlite3", {})


def test_total_uncompressed_size_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(gtfs_flix, "FLIX_GTFS_MAX_UNCOMPRESSED_BYTES", 1000)
    archive_path = tmp_path / "big.zip"
    _zip(archive_path, {"stops.txt": bytes(range(256)) * 10})
    with pytest.raises(ValueError, match="zu groß"):
        gtfs_flix._build_database(archive_path, tmp_path / "out.sqlite3", {})
