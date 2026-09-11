from datetime import datetime, timezone

import pytest

from conftest import FakeClient, FakeResponse, load_fixture, load_json_fixture
from jobsearch.http import FetchError
from jobsearch.sources.ashby import AshbySource
from jobsearch.sources.greenhouse import GreenhouseSource
from jobsearch.sources.indeed import IndeedSource
from jobsearch.sources.lever import LeverSource
from jobsearch.sources.linkedin import LinkedInSource
from jobsearch.sources.workable import WorkableSource


def test_greenhouse_normalises_postings():
    client = FakeClient({"boards-api.greenhouse.io": FakeResponse(json_body=load_json_fixture("greenhouse.json"))})
    jobs = list(GreenhouseSource({"boards": ["acme"]}, client).fetch())

    # The third fixture posting has no URL and must be dropped, not crash.
    assert len(jobs) == 2
    job = jobs[0]
    assert job.company == "Acme Corp"
    assert job.title == "Senior Backend Engineer"
    assert job.location == "New York, NY"
    assert job.department == "Engineering"
    assert job.source_id == "acme:4512345"
    # gh_src is a tracking param and must not survive into the apply link.
    assert job.apply_url == "https://boards.greenhouse.io/acme/jobs/4512345"
    # content arrives as escaped HTML and should be flattened to plain text.
    assert job.description == "We are hiring a backend engineer."


def test_greenhouse_accepts_a_bare_string_board():
    client = FakeClient({"greenhouse": FakeResponse(json_body=load_json_fixture("greenhouse.json"))})
    assert list(GreenhouseSource({"boards": "acme"}, client).fetch())


def test_greenhouse_skips_a_failing_board_and_keeps_going():
    client = FakeClient({
        "boards/dead": FetchError("HTTP 404"),
        "boards/acme": FakeResponse(json_body=load_json_fixture("greenhouse.json")),
    })
    jobs = list(GreenhouseSource({"boards": ["dead", "acme"]}, client).fetch())
    assert len(jobs) == 2


def test_greenhouse_requires_boards_option():
    with pytest.raises(KeyError, match="requires option 'boards'"):
        list(GreenhouseSource({}, FakeClient()).fetch())


def test_lever_parses_epoch_millis_and_workplace_type():
    client = FakeClient({"api.lever.co": FakeResponse(json_body=load_json_fixture("lever.json"))})
    jobs = list(LeverSource({"companies": ["globex"]}, client).fetch())

    assert len(jobs) == 2
    assert jobs[0].title == "Full Stack Engineer"
    assert jobs[0].remote is True
    assert jobs[0].department == "Product Engineering"
    assert jobs[0].posted_at == datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert jobs[1].remote is False


def test_ashby_carries_compensation_into_extra():
    client = FakeClient({"ashbyhq.com": FakeResponse(json_body=load_json_fixture("ashby.json"))})
    jobs = list(AshbySource({"boards": ["initech"]}, client).fetch())

    assert len(jobs) == 1
    assert jobs[0].company == "Initech"
    assert jobs[0].remote is True
    assert jobs[0].extra["compensation"]["summary"] == "$180K - $220K"
    assert jobs[0].posted_at.year == 2026


def test_workable_flattens_the_location_object():
    client = FakeClient({"apply.workable.com": FakeResponse(json_body=load_json_fixture("workable.json"))})
    jobs = list(WorkableSource({"accounts": ["hooli"]}, client).fetch())

    assert len(jobs) == 1
    assert jobs[0].company == "Hooli"
    assert jobs[0].location == "London, England, United Kingdom"
    assert jobs[0].remote is True
    assert jobs[0].source_id == "hooli:ABC123DEF4"


def test_linkedin_parses_guest_cards():
    source = LinkedInSource({"queries": []}, FakeClient())
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    jobs = list(source.parse(load_fixture("linkedin.html"), now=now))

    # The third card has no link and must be skipped.
    assert len(jobs) == 2
    assert jobs[0].company == "Umbrella Corp"
    assert jobs[0].title == "Senior Backend Engineer"
    assert jobs[0].source_id == "4051234567"
    assert "?" not in jobs[0].url
    assert jobs[0].posted_at.date().isoformat() == "2026-09-09"
    # The second card has no datetime attribute, only "3 weeks ago".
    assert jobs[1].posted_at.date().isoformat() == "2026-08-21"


def test_linkedin_survives_a_block_without_failing_the_run(caplog):
    client = FakeClient({"linkedin.com": FetchError("HTTP 999")})
    source = LinkedInSource({"queries": [{"keywords": "swe", "location": "NY"}]}, client)
    assert list(source.fetch()) == []
    assert "blocked or unavailable" in caplog.text


def test_linkedin_applies_time_and_workplace_filters():
    client = FakeClient({"linkedin.com": FakeResponse(load_fixture("linkedin.html"))})
    source = LinkedInSource(
        {"queries": [{"keywords": "swe", "location": "NY"}],
         "posted_within": "day", "workplace": "remote", "max_pages": 1},
        client,
    )
    list(source.fetch())
    url = client.requests[0][0]
    assert "f_TPR=r86400" in url and "f_WT=2" in url


def test_indeed_extracts_the_embedded_job_json():
    source = IndeedSource({"queries": []}, FakeClient())
    jobs = list(source.parse(load_fixture("indeed.html")))

    # The third result has no jobkey and must be dropped.
    assert len(jobs) == 2
    assert jobs[0].title == "Software Engineer II"
    assert jobs[0].company == "Vandelay Industries"
    assert jobs[0].remote is True
    assert jobs[0].url == "https://www.indeed.com/viewjob?jk=a1b2c3d4e5f60718"
    assert jobs[0].posted_at.year == 2026
    # displayTitle is the fallback when title is absent.
    assert jobs[1].title == "Warehouse Associate"


def test_indeed_returns_nothing_for_an_unparseable_page():
    source = IndeedSource({"queries": []}, FakeClient())
    assert list(source.parse("<html>nope</html>")) == []


def test_indeed_reports_a_bot_challenge_as_a_warning(caplog):
    client = FakeClient({"/jobs?": FakeResponse("<html>Attention Required! | Cloudflare</html>")})
    source = IndeedSource({"queries": [{"what": "swe", "where": "Remote"}]}, client)
    assert list(source.fetch()) == []
    assert "blocked or unavailable" in caplog.text


def test_indeed_honours_a_custom_base_url():
    client = FakeClient({"feed.example.com": FakeResponse(load_fixture("indeed.html"))})
    source = IndeedSource(
        {"queries": [{"what": "swe", "where": "Remote"}],
         "base_url": "https://feed.example.com", "max_pages": 1},
        client,
    )
    jobs = list(source.fetch())
    assert jobs[0].url.startswith("https://feed.example.com/viewjob")
