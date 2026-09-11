from datetime import datetime, timedelta, timezone

from jobsearch.config import MatchConfig
from jobsearch.matching import evaluate, filter_jobs, looks_remote
from jobsearch.models import Job

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def make_job(**kwargs) -> Job:
    defaults = dict(source="greenhouse", company="Acme", title="Backend Engineer",
                    url="https://x/1", location="New York, NY")
    defaults.update(kwargs)
    return Job(**defaults)


def test_empty_config_matches_everything():
    assert evaluate(make_job(), MatchConfig(), NOW).matched


def test_title_include_is_case_insensitive():
    match = MatchConfig(title_include=["backend"])
    assert evaluate(make_job(title="Senior BACKEND Engineer"), match, NOW).matched


def test_title_include_rejects_a_non_match():
    result = evaluate(make_job(title="Recruiter"), MatchConfig(title_include=["backend"]), NOW)
    assert not result.matched
    assert "title_include" in result.reason


def test_title_exclude_wins_over_include():
    match = MatchConfig(title_include=["engineer"], title_exclude=["intern"])
    result = evaluate(make_job(title="Backend Engineer Intern"), match, NOW)
    assert not result.matched
    assert "intern" in result.reason


def test_company_exclude():
    result = evaluate(make_job(), MatchConfig(company_exclude=["acme"]), NOW)
    assert not result.matched


def test_location_exclude():
    result = evaluate(make_job(location="Tokyo, Japan"), MatchConfig(location_exclude=["japan"]), NOW)
    assert not result.matched


def test_location_include_accepts_a_matching_location():
    assert evaluate(make_job(), MatchConfig(location_include=["new york"]), NOW).matched


def test_location_include_rejects_an_unlisted_location():
    result = evaluate(make_job(location="Berlin"), MatchConfig(location_include=["new york"]), NOW)
    assert not result.matched


def test_remote_roles_satisfy_a_location_filter():
    # A remote role is workable from the locations the user cares about.
    job = make_job(location="Remote - Worldwide")
    assert evaluate(job, MatchConfig(location_include=["new york"]), NOW).matched


def test_remote_only_rejects_onsite():
    result = evaluate(make_job(), MatchConfig(remote_only=True), NOW)
    assert not result.matched
    assert "remote" in result.reason


def test_remote_only_accepts_an_explicit_remote_flag():
    assert evaluate(make_job(remote=True), MatchConfig(remote_only=True), NOW).matched


def test_explicit_remote_false_beats_a_location_hint():
    # The source knows better than a keyword in the location string.
    job = make_job(location="Remote-friendly office, NYC", remote=False)
    assert not looks_remote(job)


def test_description_exclude():
    job = make_job(description="Requires an active security clearance.")
    result = evaluate(job, MatchConfig(description_exclude=["security clearance"]), NOW)
    assert not result.matched


def test_max_age_days_rejects_a_stale_posting():
    job = make_job(posted_at=NOW - timedelta(days=40))
    result = evaluate(job, MatchConfig(max_age_days=21), NOW)
    assert not result.matched
    assert "40 days ago" in result.reason


def test_max_age_days_keeps_a_posting_with_no_date():
    # Some boards omit timestamps; dropping those would hide real matches.
    assert evaluate(make_job(posted_at=None), MatchConfig(max_age_days=1), NOW).matched


def test_filter_jobs_returns_only_matches():
    jobs = [make_job(title="Backend Engineer"), make_job(title="Sales Lead")]
    assert len(filter_jobs(jobs, MatchConfig(title_include=["engineer"]), NOW)) == 1


def test_keywords_match_as_substrings_not_whole_words():
    # Documented behaviour: "engineer" deliberately also matches "Engineering",
    # so narrow with title_exclude rather than expecting word boundaries.
    match = MatchConfig(title_include=["engineer"])
    assert evaluate(make_job(title="Engineering Manager"), match, NOW).matched
    assert not evaluate(
        make_job(title="Engineering Manager"),
        MatchConfig(title_include=["engineer"], title_exclude=["manager"]),
        NOW,
    ).matched
