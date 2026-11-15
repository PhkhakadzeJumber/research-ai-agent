"""Tests for src/retrieval/cache.py (uses pytest's temporary folders, never the real .cache/)."""

from datetime import datetime, timedelta, timezone

from src.retrieval.cache import FileResponseCache, cache_key, public_params

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, moment=T0):
        self.moment = moment

    def __call__(self):
        return self.moment


def test_secret_params_are_excluded_from_key_and_stored_params():
    with_key = {"search": "rag", "api_key": "SECRET", "mailto": "me@example.org"}
    without = {"search": "rag"}
    assert public_params(with_key) == {"search": "rag"}
    assert cache_key("openalex", "/works", with_key) == cache_key("openalex", "/works", without)


def test_key_depends_on_source_path_and_params():
    base = cache_key("openalex", "/works", {"search": "rag"})
    assert base != cache_key("arxiv", "/works", {"search": "rag"})
    assert base != cache_key("openalex", "/other", {"search": "rag"})
    assert base != cache_key("openalex", "/works", {"search": "rag", "per-page": 5})
    # parameter order doesn't matter
    assert cache_key("s", "/p", {"a": 1, "b": 2}) == cache_key("s", "/p", {"b": 2, "a": 1})


def test_put_then_get(tmp_path):
    cache = FileResponseCache(tmp_path, clock=FakeClock())
    cache.put("openalex", "/works", {"search": "rag"}, '{"results": []}')
    assert cache.get("openalex", "/works", {"search": "rag"}) == '{"results": []}'
    assert (cache.hits, cache.misses) == (1, 0)


def test_missing_entry_is_a_miss(tmp_path):
    cache = FileResponseCache(tmp_path)
    assert cache.get("openalex", "/works", {"search": "nothing"}) is None
    assert cache.misses == 1


def test_expired_entry_is_a_miss(tmp_path):
    clock = FakeClock()
    cache = FileResponseCache(tmp_path, ttl=timedelta(days=7), clock=clock)
    cache.put("arxiv", "/api/query", {"q": "x"}, "<feed/>")

    clock.moment = T0 + timedelta(days=6, hours=23)
    assert cache.get("arxiv", "/api/query", {"q": "x"}) == "<feed/>"
    clock.moment = T0 + timedelta(days=7, minutes=1)
    assert cache.get("arxiv", "/api/query", {"q": "x"}) is None


def test_secrets_are_never_written_to_disk(tmp_path):
    cache = FileResponseCache(tmp_path, clock=FakeClock())
    cache.put("openalex", "/works", {"search": "rag", "api_key": "SECRET-KEY", "mailto": "me@example.org"}, "{}")
    stored = "".join(file.read_text(encoding="utf-8") for file in tmp_path.rglob("*.json"))
    assert "SECRET-KEY" not in stored and "me@example.org" not in stored
    assert '"fetched_at": "2026-11-15T16:00:00+04:00"' in stored  # Tbilisi time with offset


def test_corrupted_entry_is_treated_as_a_miss(tmp_path):
    cache = FileResponseCache(tmp_path, clock=FakeClock())
    cache.put("openalex", "/works", {"search": "rag"}, "{}")
    next(tmp_path.rglob("*.json")).write_text("not json at all", encoding="utf-8")
    assert cache.get("openalex", "/works", {"search": "rag"}) is None


def test_write_failure_is_not_raised(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the cache folder should be", encoding="utf-8")
    cache = FileResponseCache(blocker)
    cache.put("openalex", "/works", {"search": "rag"}, "{}")  # must not raise
    assert cache.get("openalex", "/works", {"search": "rag"}) is None
