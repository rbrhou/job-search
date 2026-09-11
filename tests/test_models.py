from datetime import datetime, timedelta, timezone

from jobsearch.models import Job, canonical_url


def test_canonical_url_strips_tracking_and_normalises():
    url = "https://Boards.Greenhouse.IO/Acme/jobs/123/?gh_src=abc&utm_medium=x&keep=1"
    assert canonical_url(url) == "https://boards.greenhouse.io/Acme/jobs/123?keep=1"


def test_canonical_url_handles_empty():
    assert canonical_url("") == ""


def test_fingerprint_is_stable_across_tracking_params():
    a = Job(source="x", company="Acme", title="SWE", url="https://x/j/1?utm_source=a")
    b = Job(source="x", company="Acme", title="SWE", url="https://x/j/1?utm_source=b")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_prefers_source_id_over_url():
    a = Job(source="x", company="Acme", title="SWE", url="https://old/1", source_id="42")
    b = Job(source="x", company="Acme", title="SWE", url="https://new/1", source_id="42")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_differs_between_sources():
    a = Job(source="greenhouse", company="Acme", title="SWE", url="https://x/1")
    b = Job(source="lever", company="Acme", title="SWE", url="https://x/1")
    assert a.fingerprint != b.fingerprint


def test_age_days_treats_naive_timestamps_as_utc():
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    job = Job(source="x", company="A", title="T", url="u", posted_at=datetime(2026, 9, 1))
    assert round(job.age_days(now)) == 10


def test_age_days_is_none_without_a_timestamp():
    assert Job(source="x", company="A", title="T", url="u").age_days() is None


def test_to_dict_emits_the_canonical_url():
    job = Job(source="x", company="A", title="T", url="https://x/1?utm_source=q",
              posted_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    data = job.to_dict()
    assert data["url"] == "https://x/1"
    assert data["posted_at"].startswith("2026-09-01")
