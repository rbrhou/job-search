from datetime import datetime, timezone

import pytest

from conftest import FakeClient, FakeResponse
from jobsearch.models import Job
from jobsearch.notifiers.discord import (
    MAX_EMBEDS_PER_MESSAGE,
    MAX_TOTAL_EMBED_CHARS,
    DiscordError,
    DiscordNotifier,
    _embed_chars,
)

HOOK = "https://discord.com/api/webhooks/1/token"


def make_job(n: int = 1, **kwargs) -> Job:
    defaults = dict(
        source="greenhouse", company="Acme", title=f"Backend Engineer {n}",
        url=f"https://boards.greenhouse.io/acme/jobs/{n}?gh_src=x",
        location="New York, NY", department="Engineering",
        posted_at=datetime(2026, 9, 9, tzinfo=timezone.utc), source_id=str(n),
    )
    defaults.update(kwargs)
    return Job(**defaults)


def notifier(client, **options) -> DiscordNotifier:
    return DiscordNotifier({"webhook_url": HOOK, **options}, client)


def test_embed_carries_title_link_and_fields():
    embed = notifier(FakeClient()).build_embed(make_job())
    assert embed["title"] == "Backend Engineer 1"
    # The apply link must be the canonical URL, with tracking stripped.
    assert embed["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert embed["author"]["name"] == "Acme"
    assert embed["footer"]["text"] == "via greenhouse"
    names = [f["name"] for f in embed["fields"]]
    assert names == ["Location", "Team", "Posted"]
    # Discord renders <t:epoch:R> as a live relative time.
    assert embed["fields"][2]["value"].startswith("<t:") and embed["fields"][2]["value"].endswith(":R>")


def test_embed_omits_fields_that_have_no_value():
    embed = notifier(FakeClient()).build_embed(
        Job(source="lever", company="A", title="T", url="https://x/1")
    )
    assert "fields" not in embed
    assert "description" not in embed


def test_embed_truncates_an_overlong_title():
    embed = notifier(FakeClient()).build_embed(make_job(title="X" * 400))
    assert len(embed["title"]) == 256
    assert embed["title"].endswith("…")


def test_send_posts_one_message_for_a_small_digest():
    client = FakeClient()
    notifier(client).send([make_job(1), make_job(2)])

    assert len(client.posts) == 1
    payload = client.posts[0][1]["json"]
    assert len(payload["embeds"]) == 2
    assert payload["content"] == "**2 new job postings**"
    assert payload["username"] == "Job Search"


def test_content_is_singular_for_one_job():
    client = FakeClient()
    notifier(client).send([make_job(1)])
    assert client.posts[0][1]["json"]["content"] == "**1 new job posting**"


def test_send_splits_batches_at_the_embed_limit():
    client = FakeClient()
    notifier(client).send([make_job(n) for n in range(25)])

    assert len(client.posts) == 3
    counts = [len(p[1]["json"]["embeds"]) for p in client.posts]
    assert counts == [MAX_EMBEDS_PER_MESSAGE, MAX_EMBEDS_PER_MESSAGE, 5]


def test_only_the_first_message_carries_the_header_and_mention():
    client = FakeClient()
    notifier(client, mention="<@&42>").send([make_job(n) for n in range(15)])

    first, second = (p[1]["json"] for p in client.posts)
    assert first["content"].startswith("<@&42>")
    # Pinging once per batch would be three pings for one digest.
    assert "content" not in second


def test_mentions_are_suppressed_when_none_is_configured():
    client = FakeClient()
    notifier(client).send([make_job(1)])
    assert client.posts[0][1]["json"]["allowed_mentions"] == {"parse": []}


def test_batches_respect_the_total_character_ceiling():
    # build_embed caps each embed well under the limit, so exercise the guard
    # directly: ten near-maximal embeds would breach Discord's 6000-char ceiling.
    fat = [{"title": "T" * 256, "description": "D" * 500} for _ in range(10)]
    batches = list(notifier(FakeClient())._batch(fat))

    assert len(batches) > 1
    for batch in batches:
        assert sum(_embed_chars(e) for e in batch) <= MAX_TOTAL_EMBED_CHARS


def test_a_single_oversized_embed_is_still_sent():
    # Splitting can never drop a job, even one bigger than the ceiling alone.
    huge = [{"title": "T", "description": "D" * (MAX_TOTAL_EMBED_CHARS + 100)}]
    assert list(notifier(FakeClient())._batch(huge)) == [huge]


def test_a_rate_limit_is_retried_after_the_requested_delay(monkeypatch):
    slept = []
    monkeypatch.setattr("jobsearch.notifiers.discord.time.sleep", slept.append)
    responses = iter([
        FakeResponse(status_code=429, json_body={"retry_after": 0.75}),
        FakeResponse(status_code=204, json_body={}),
    ])
    client = FakeClient({"discord.com": lambda url: next(responses)})

    notifier(client).send([make_job(1)])

    assert slept == [0.75]
    assert len(client.posts) == 2


def test_an_error_response_is_raised():
    client = FakeClient({"discord.com": FakeResponse("bad webhook", status_code=404, json_body={})})
    with pytest.raises(DiscordError, match="HTTP 404"):
        notifier(client).send([make_job(1)])


def test_webhook_url_is_required():
    with pytest.raises(KeyError, match="requires option 'webhook_url'"):
        DiscordNotifier({}, FakeClient()).send([make_job(1)])
