from __future__ import annotations

import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import reisevergleich.history_cache as history_cache
from reisevergleich.history_cache import DetailSpec


with tempfile.TemporaryDirectory(prefix="traviorel-history-concurrency-") as directory:
    original_cache_dir = history_cache.HISTORY_CACHE_DIR
    original_query = history_cache.query_remote_month
    original_put = history_cache.cache.history_detail_put
    original_prune = history_cache._prune_lru
    active = 0
    maximum = 0
    guard = threading.Lock()

    def fake_remote(_spec: DetailSpec, target: Path) -> tuple[int, str]:
        global active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.05)
            target.write_bytes(b"test-detail-cache")
            return 1, "test"
        finally:
            with guard:
                active -= 1

    history_cache.HISTORY_CACHE_DIR = str(Path(directory) / "history")
    history_cache.query_remote_month = fake_remote
    history_cache.cache.history_detail_put = lambda _metadata: None
    history_cache._prune_lru = lambda: None
    try:
        specs = [DetailSpec("ICE", str(100 + index), 2026, 1) for index in range(8)]
        with ThreadPoolExecutor(max_workers=len(specs)) as executor:
            paths = list(executor.map(history_cache._write_detail, specs))
    finally:
        history_cache.HISTORY_CACHE_DIR = original_cache_dir
        history_cache.query_remote_month = original_query
        history_cache.cache.history_detail_put = original_put
        history_cache._prune_lru = original_prune

    assert all(path.is_file() for path in paths)
    assert maximum == 2, f"expected two concurrent remote fills, observed {maximum}"


class FakeResponse:
    status_code = 200

    def __init__(self):
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size=None):
        assert chunk_size == 1024 * 1024
        yield b"parquet-"
        yield b"payload"

    def close(self) -> None:
        self.closed = True


with tempfile.TemporaryDirectory(prefix="traviorel-history-curl-") as directory:
    original_get = history_cache.curl_requests.get
    response = FakeResponse()

    def fake_get(url, **kwargs):
        assert url == "https://huggingface.co/test.parquet"
        assert kwargs["stream"] is True and kwargs["allow_redirects"] is True
        return response

    history_cache.curl_requests.get = fake_get
    try:
        downloaded = history_cache._download_month("https://huggingface.co/test.parquet", Path(directory))
    finally:
        history_cache.curl_requests.get = original_get
    assert downloaded.read_bytes() == b"parquet-payload"
    assert response.closed is True

print("Prozessweiter History-Remote-Limiter (maximal 2 parallele Fills): OK")
print("curl_cffi History-Download-Fallback: OK")
