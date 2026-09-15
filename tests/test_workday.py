from datetime import datetime, timezone

import pytest

from conftest import FakeClient, FakeResponse, load_json_fixture
from jobsearch.http import FetchError
from jobsearch.sources.workday import (
    WorkdaySource,
    WorkdayTargetError,
    parse_posted,
    parse_target,
)

NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
URL = "https://sunlife.wd3.myworkdayjobs.com/en-US/Experienced-Jobs"


class FakeWorkdayClient(FakeClient):
    """Records POST bodies so pagination can be asserted."""

    def __init__(self, pages):
        super().__init__()
        self.pages = list(pages)
        self.bodies = []

    def post_json(self, url, payload, **kwargs):
        self.bodies.append(payload)
        if not self.pages:
            return {"total": 0, "jobPostings": []}
        return self.pages.pop(0)


# --- target parsing -----------------------------------------------------------

def test_a_career_site_url_resolves_to_host_tenant_and_site():
    assert parse_target({"url": URL}) == (
        "sunlife.wd3.myworkdayjobs.com", "sunlife", "Experienced-Jobs"
    )


def test_a_bare_string_is_treated_as_a_url():
    assert parse_target(URL)[2] == "Experienced-Jobs"


def test_the_locale_segment_is_optional():
    assert parse_target("https://rbc.wd3.myworkdayjobs.com/RBCJOBS") == (
        "rbc.wd3.myworkdayjobs.com", "rbc", "RBCJOBS"
    )


def test_a_trailing_slash_does_not_become_the_site_name():
    assert parse_target("https://bmo.wd3.myworkdayjobs.com/en-US/External/")[2] == "External"


def test_an_explicit_tenant_overrides_the_host_prefix():
    # Some tenants do not match their hostname's first label.
    target = parse_target({"url": URL, "tenant": "sunlifefinancial"})
    assert target == ("sunlife.wd3.myworkdayjobs.com", "sunlifefinancial", "Experienced-Jobs")


def test_host_tenant_and_site_can_be_given_explicitly():
    assert parse_target(
        {"host": "h.example.com", "tenant": "t", "site": "s"}
    ) == ("h.example.com", "t", "s")


def test_a_url_without_a_site_name_is_rejected():
    with pytest.raises(WorkdayTargetError, match="no site name"):
        parse_target("https://rbc.wd3.myworkdayjobs.com/en-US")


def test_a_relative_url_is_rejected():
    with pytest.raises(WorkdayTargetError, match="absolute"):
        parse_target("/en-US/RBCJOBS")


def test_an_incomplete_explicit_entry_names_what_is_missing():
    with pytest.raises(WorkdayTargetError, match="missing tenant, site"):
        parse_target({"host": "h.example.com"})


# --- posted-on prose ----------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected_days",
    [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3),
     ("Posted 30+ Days Ago", 30), ("Posted 2 Months Ago", 60)],
)
def test_posted_prose_becomes_a_timestamp(text, expected_days):
    parsed = parse_posted(text, NOW)
    assert round((NOW - parsed).days) == expected_days


def test_an_hours_old_posting_keeps_hour_precision():
    from datetime import timedelta

    assert parse_posted("Posted 5 Hours Ago", NOW) == NOW - timedelta(hours=5)


def test_unparseable_posted_text_yields_no_date():
    assert parse_posted("Posted a while back", NOW) is None
    assert parse_posted(None, NOW) is None


# --- fetching -----------------------------------------------------------------

def make_source(client, **options):
    options.setdefault("employers", [{"name": "Sun Life", "url": URL}])
    return WorkdaySource(options, client)


def test_postings_are_normalised():
    client = FakeWorkdayClient([load_json_fixture("workday_page1.json")])
    jobs = list(make_source(client, max_pages=1).fetch())

    # The fourth fixture posting has no externalPath and must be dropped.
    assert len(jobs) == 3
    job = jobs[0]
    assert job.company == "Sun Life"
    assert job.title == "Actuarial Analyst Co-op (Summer 2027)"
    assert job.location == "Toronto, Ontario, Canada"
    assert job.url == (
        "https://sunlife.wd3.myworkdayjobs.com/en-US/Experienced-Jobs"
        "/job/Toronto-Ontario/Actuarial-Analyst-Co-op--Summer-2027-_JR00089421"
    )
    assert job.source_id.startswith("sunlife:/job/Toronto-Ontario/")
    assert job.extra["requisition"] == ["JR00089421"]


def test_a_remote_location_sets_the_remote_flag():
    client = FakeWorkdayClient([load_json_fixture("workday_page1.json")])
    jobs = list(make_source(client, max_pages=1).fetch())
    assert jobs[1].remote is True
    assert jobs[0].remote is None


def test_a_multi_location_posting_keeps_workdays_placeholder_text():
    # "3 Locations" names no city, so a location filter cannot match it — worth
    # asserting so the behaviour is not mistaken for a parsing bug.
    client = FakeWorkdayClient([load_json_fixture("workday_page1.json")])
    jobs = list(make_source(client, max_pages=1).fetch())
    assert jobs[2].location == "3 Locations"


def test_pagination_walks_offsets_until_total_is_reached():
    pages = [load_json_fixture("workday_page1.json"), load_json_fixture("workday_page2.json")]
    client = FakeWorkdayClient(pages)
    jobs = list(make_source(client).fetch())

    assert len(jobs) == 4
    # Page 1 yields 4 of the 5 the API reports, so exactly one more page is
    # fetched — no blind extra request once the total is accounted for.
    assert [b["offset"] for b in client.bodies] == [0, 20]
    assert client.bodies[0]["limit"] == 20


def test_pagination_stops_on_an_empty_page():
    client = FakeWorkdayClient([load_json_fixture("workday_page2.json"), {"total": 99, "jobPostings": []}])
    list(make_source(client).fetch())
    assert len(client.bodies) == 2


def test_max_pages_bounds_the_walk():
    pages = [{"total": 10_000, "jobPostings": load_json_fixture("workday_page2.json")["jobPostings"]}] * 5
    client = FakeWorkdayClient(pages)
    list(make_source(client, max_pages=2).fetch())
    assert len(client.bodies) == 2


def test_search_text_is_forwarded_to_the_api():
    client = FakeWorkdayClient([{"total": 0, "jobPostings": []}])
    list(make_source(client, search_text="actuarial").fetch())
    assert client.bodies[0]["searchText"] == "actuarial"


def test_a_wrong_site_name_is_logged_and_skipped(caplog):
    class Failing(FakeClient):
        def post_json(self, url, payload, **kwargs):
            raise FetchError("HTTP 404")

    assert list(make_source(Failing()).fetch()) == []
    assert "skipping Sun Life" in caplog.text


def test_a_malformed_employer_entry_does_not_stop_the_others(caplog):
    client = FakeWorkdayClient([load_json_fixture("workday_page2.json")])
    source = WorkdaySource(
        {"employers": [{"name": "Broken"}, {"name": "Sun Life", "url": URL}]}, client
    )
    jobs = list(source.fetch())
    assert len(jobs) == 1
    assert "malformed employer entry" in caplog.text


def test_employers_is_required():
    with pytest.raises(KeyError, match="requires option 'employers'"):
        list(WorkdaySource({}, FakeClient()).fetch())


def test_several_search_terms_each_get_their_own_paginated_query():
    # One query cannot span "intern", "co-op" and "student"; each is searched.
    page = load_json_fixture("workday_page2.json")
    client = FakeWorkdayClient([page, page, page])
    source = make_source(client, search_texts=["intern", "co-op", "student"], max_pages=1)
    jobs = list(source.fetch())

    assert [b["searchText"] for b in client.bodies] == ["intern", "co-op", "student"]
    assert len(jobs) == 3  # the same posting three times; dedup happens downstream


def test_search_texts_can_be_set_per_employer():
    client = FakeWorkdayClient([{"total": 0, "jobPostings": []}])
    source = WorkdaySource(
        {"employers": [{"name": "X", "url": URL, "search_texts": ["actuarial"]}],
         "search_texts": ["intern"]},
        client,
    )
    list(source.fetch())
    assert client.bodies[0]["searchText"] == "actuarial"


def test_duplicate_postings_across_terms_collapse_on_fingerprint():
    page = load_json_fixture("workday_page2.json")
    client = FakeWorkdayClient([page, page])
    jobs = list(make_source(client, search_texts=["intern", "co-op"], max_pages=1).fetch())
    assert len({job.fingerprint for job in jobs}) == 1
