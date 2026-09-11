import json
from datetime import datetime, timedelta, timezone

from jobsearch.models import Job
from jobsearch.store import SeenStore

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def make_job(n: int) -> Job:
    return Job(source="greenhouse", company="Acme", title=f"Engineer {n}",
               url=f"https://x/{n}", source_id=str(n))


def test_everything_is_new_against_an_empty_store(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    assert len(store.filter_new([make_job(1), make_job(2)])) == 2


def test_recorded_jobs_are_not_new_on_the_next_run(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.record([make_job(1)], now=NOW)
    store.save()

    assert len(SeenStore(path).filter_new([make_job(1), make_job(2)])) == 1


def test_filter_new_deduplicates_within_one_batch(tmp_path):
    # The same role often shows up on both a company board and an aggregator.
    store = SeenStore(tmp_path / "seen.json")
    assert len(store.filter_new([make_job(1), make_job(1)])) == 1


def test_save_writes_readable_sorted_json(tmp_path):
    path = tmp_path / "nested" / "seen.json"
    store = SeenStore(path)
    store.record([make_job(2), make_job(1)], now=NOW)
    store.save()

    payload = json.loads(path.read_text())
    assert payload["version"] == 1
    assert list(payload["jobs"]) == sorted(payload["jobs"])
    entry = next(iter(payload["jobs"].values()))
    assert entry["company"] == "Acme" and entry["url"].startswith("https://x/")


def test_record_keeps_the_original_first_seen(tmp_path):
    store = SeenStore(tmp_path / "seen.json")
    store.record([make_job(1)], now=NOW)
    store.record([make_job(1)], now=NOW + timedelta(days=5))
    assert store.entries[make_job(1).fingerprint]["first_seen"] == NOW.isoformat()


def test_prune_drops_entries_past_the_retention_window(tmp_path):
    store = SeenStore(tmp_path / "seen.json", retention_days=30)
    store.record([make_job(1)], now=NOW - timedelta(days=90))
    store.record([make_job(2)], now=NOW)
    assert store.prune(now=NOW) == 1
    assert len(store) == 1


def test_prune_ignores_entries_with_an_unparseable_date(tmp_path):
    store = SeenStore(tmp_path / "seen.json", retention_days=30)
    store.entries["bad"] = {"first_seen": "not-a-date"}
    assert store.prune(now=NOW) == 0
    assert "bad" in store.entries


def test_a_corrupt_state_file_does_not_crash_the_run(tmp_path, caplog):
    path = tmp_path / "seen.json"
    path.write_text("{ this is not json")
    store = SeenStore(path)
    assert len(store) == 0
    assert "unreadable" in caplog.text


def test_a_future_schema_version_is_ignored(tmp_path, caplog):
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"version": 99, "jobs": {"abc": {}}}))
    assert len(SeenStore(path)) == 0
    assert "unknown version" in caplog.text


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.record([make_job(1)], now=NOW)
    store.save()
    assert [p.name for p in tmp_path.iterdir()] == ["seen.json"]
