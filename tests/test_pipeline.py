import pytest

from conftest import FakeClient, FakeResponse, load_json_fixture
from jobsearch.config import Config
from jobsearch.pipeline import OptInRequired, run
from jobsearch.store import SeenStore


def base_config(tmp_path, **overrides) -> dict:
    config = {
        "sources": [{"type": "greenhouse", "boards": ["acme"]}],
        "notifiers": [{"type": "discord", "webhook_url": "https://discord.com/api/webhooks/1/t"}],
        "match": {"title_include": ["backend"]},
        "state_path": str(tmp_path / "seen.json"),
        "notify_on_first_run": True,
    }
    config.update(overrides)
    return config


@pytest.fixture
def patched_client(monkeypatch):
    """Route every HTTP call in the pipeline through a FakeClient."""
    client = FakeClient({
        "greenhouse": FakeResponse(json_body=load_json_fixture("greenhouse.json")),
        "discord.com": FakeResponse("", 204, json_body={}),
    })
    monkeypatch.setattr("jobsearch.pipeline.HttpClient", lambda **kwargs: client)
    return client


def test_run_fetches_filters_notifies_and_records(tmp_path, patched_client):
    config = Config.from_dict(base_config(tmp_path))
    report = run(config)

    assert report.fetched == 2          # third fixture posting has no URL
    assert report.matched == 1          # "Engineering Manager" fails title_include
    assert report.new == 1
    assert report.notified == 1
    assert len(patched_client.posts) == 1

    titles = [e["title"] for e in patched_client.posts[0][1]["json"]["embeds"]]
    assert titles == ["Senior Backend Engineer"]
    assert len(SeenStore(config.state_path)) == 1


def test_a_second_run_notifies_nothing_new(tmp_path, patched_client):
    config = Config.from_dict(base_config(tmp_path))
    run(config)
    patched_client.posts.clear()

    report = run(config)
    assert report.new == 0
    assert report.notified == 0
    assert patched_client.posts == []


def test_first_run_seeds_silently_by_default(tmp_path, patched_client):
    config = Config.from_dict(base_config(tmp_path, notify_on_first_run=False))
    report = run(config)

    # Alerting on every posting a board already has would bury the channel.
    assert report.seeded == 1
    assert report.notified == 0
    assert patched_client.posts == []
    # The baseline is still recorded, so the next genuinely new job does alert.
    assert len(SeenStore(config.state_path)) == 1
    assert "seeded" in report.summary()


def test_dry_run_neither_notifies_nor_writes_state(tmp_path, patched_client, capsys):
    config = Config.from_dict(base_config(tmp_path))
    report = run(config, dry_run=True)

    assert report.new == 1
    assert report.notified == 0
    assert patched_client.posts == []
    assert not config.state_path.exists()
    assert "Senior Backend Engineer" in capsys.readouterr().out


def test_a_failing_source_is_reported_without_sinking_the_run(tmp_path, monkeypatch):
    client = FakeClient({
        "boards/acme": FakeResponse(json_body=load_json_fixture("greenhouse.json")),
        "api.lever.co": RuntimeError("lever exploded"),
        "discord.com": FakeResponse("", 204, json_body={}),
    })
    monkeypatch.setattr("jobsearch.pipeline.HttpClient", lambda **kwargs: client)
    config = Config.from_dict(base_config(tmp_path, sources=[
        {"type": "greenhouse", "boards": ["acme"]},
        {"type": "lever", "companies": ["broken"]},
    ]))

    report = run(config)
    assert report.notified == 1
    assert len(report.errors) == 1 and "lever" in report.errors[0]


def test_a_notifier_failure_leaves_jobs_unrecorded_for_a_retry(tmp_path, monkeypatch):
    client = FakeClient({
        "greenhouse": FakeResponse(json_body=load_json_fixture("greenhouse.json")),
        "discord.com": FakeResponse("server error", 500, json_body={}),
    })
    monkeypatch.setattr("jobsearch.pipeline.HttpClient", lambda **kwargs: client)
    config = Config.from_dict(base_config(tmp_path))

    report = run(config)
    assert report.notified == 0
    assert report.errors
    # Recording them would silently swallow the alert forever.
    assert not config.state_path.exists()


def test_max_per_run_caps_the_digest(tmp_path, patched_client):
    config = Config.from_dict(
        base_config(tmp_path, match={}, max_per_run=1)
    )
    report = run(config)

    assert report.matched == 2
    assert report.notified == 1
    embeds = patched_client.posts[0][1]["json"]["embeds"]
    assert len(embeds) == 1
    # Newest first, so a truncated digest keeps the freshest posting.
    assert embeds[0]["title"] == "Senior Backend Engineer"


def test_an_opt_in_source_without_acknowledgement_is_refused(tmp_path, patched_client):
    config = Config.from_dict(base_config(tmp_path, sources=[
        {"type": "linkedin", "queries": [{"keywords": "swe"}]},
    ]))
    with pytest.raises(OptInRequired, match="accept_terms_risk"):
        run(config)


def test_an_opt_in_source_runs_once_acknowledged(tmp_path, monkeypatch):
    client = FakeClient({
        "linkedin.com": FakeResponse("<html></html>"),
        "discord.com": FakeResponse("", 204, json_body={}),
    })
    monkeypatch.setattr("jobsearch.pipeline.HttpClient", lambda **kwargs: client)
    config = Config.from_dict(base_config(tmp_path, sources=[
        {"type": "linkedin", "accept_terms_risk": True, "queries": [{"keywords": "swe"}]},
    ]))
    assert run(config).fetched == 0
