# job-search

A scheduled job-posting notifier. It polls company job boards, keeps only the
postings that match your filters, and posts the new ones to a Discord channel
with a direct application link. State lives in the repo, so you only ever hear
about a role once.

```
sources ──► match filters ──► drop already-seen ──► Discord digest
                                     │
                                state/seen.json (committed each run)
```

## Quick start

```bash
pip install -e .
jobsearch init                 # writes config.yaml from the example
$EDITOR config.yaml            # pick companies and filters
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
jobsearch preview              # see what would be sent, without sending it
jobsearch run                  # send it
```

`jobsearch sources` lists every available source and notifier.

### Getting a Discord webhook

In Discord: **Server Settings → Integrations → Webhooks → New Webhook**, pick
the channel, then **Copy Webhook URL**. Anyone holding that URL can post to the
channel, so keep it in an environment variable or a GitHub Actions secret —
`config.yaml` is gitignored, and `config.example.yaml` refers to the webhook as
`${DISCORD_WEBHOOK_URL}` rather than inlining it.

## Running it on a schedule

`.github/workflows/job-search.yml` runs the search daily at 13:00 UTC and
commits the updated state back to the branch. To enable it:

1. Add your webhook under **Settings → Secrets and variables → Actions** as
   `DISCORD_WEBHOOK_URL`.
2. Make sure **Settings → Actions → General → Workflow permissions** is set to
   **Read and write** so the run can commit `state/seen.json`.
3. Commit a `config.yaml` (it is gitignored by default — either remove that line
   or commit it with `git add -f config.yaml`). It holds no secrets.

Change the `cron:` line to adjust frequency; it is always interpreted as UTC.
You can also trigger it by hand from the Actions tab, with a dry-run checkbox.

## Sources

| Source | What it needs | Notes |
| --- | --- | --- |
| `greenhouse` | `boards:` — the slug in `boards.greenhouse.io/<slug>` | Public board API |
| `lever` | `companies:` — the slug in `jobs.lever.co/<slug>` | Public postings API |
| `ashby` | `boards:` — the slug in `jobs.ashbyhq.com/<slug>` | Public board API |
| `workable` | `accounts:` — the slug in `apply.workable.com/<slug>` | Public widget API |
| `workday` | `employers:` — each with the career-site `url` | Public career-site API; see below |
| `linkedin` | `queries:` of keywords + location | **Opt-in**, see below |
| `indeed` | `queries:` of what + where | **Opt-in**, see below |

The first four are documented public endpoints that serve the same JSON a
company's own careers page consumes. They are the reliable path: no bot
detection, no rate-limit games, and they return the canonical apply link.

### About the Workday source

Banks, insurers and pension funds run overwhelmingly on Workday, so this source
is what makes actuarial and most finance-sector roles reachable at all. It calls
the same public JSON endpoint a company's own careers page calls — no scraping,
no bot detection.

To add an employer, open their careers page, run any search, and copy the URL
from the address bar:

```yaml
- type: workday
  search_texts: [intern, co-op, student]
  employers:
    - name: Sun Life
      url: https://sunlife.wd3.myworkdayjobs.com/en-US/Experienced-Jobs
```

Tenant, pod (`wd1`/`wd3`/`wd5`/…) and site name are all read from that URL. A
wrong URL 404s, which is logged and skipped — it costs that employer's coverage
and nothing else.

Two things worth knowing:

- **Narrow with `search_texts`.** These employers carry thousands of open roles,
  far past any page limit, so paginating the whole board is hopeless. Each term
  runs its own query, because one search can't span the vocabulary — a Canadian
  internship is variously an `intern`, a `co-op` or a `student`. Postings found
  by more than one term collapse on fingerprint.
- **Multi-location postings report `"3 Locations"`** instead of naming cities, so
  `location_include` can't match them. That's Workday's API, not a parsing bug.
- **The pod in the URL is auto-corrected.** Workday shards tenants across
  numbered pods (`wd1`, `wd3`, `wd5`, …), and `*.wdN.myworkdayjobs.com` has
  wildcard DNS, so the wrong pod fails at the application layer rather than in
  DNS: a tenant on another pod answers **422**, while a wrong site name on the
  right pod answers **404**. On a failure the source probes the other pods and
  logs the URL that worked, so paste anything and it usually still resolves.
  Set `pod_fallback: false` to disable the probing.

### About the LinkedIn and Indeed sources

You asked for these, so they are implemented and tested — but be aware of what
you are getting:

- **Both sites' terms of service prohibit automated scraping.** Using these
  sources is your call to make, not something the code decides for you, so each
  one refuses to run until you set `accept_terms_risk: true` on it.
- **Both actively block datacenter traffic.** A GitHub Actions runner shares IP
  ranges that LinkedIn answers with HTTP 999 and Indeed answers with a
  Cloudflare challenge. Expect these sources to return little or nothing from
  CI, and more from a home IP.
- **Both are parsing undocumented markup** that can change without warning.

They are written to fail soft: a block is logged as a warning and the run
continues with whatever the other sources returned, so a LinkedIn 999 never
costs you your Greenhouse alerts. If you need broad aggregator coverage that
actually works unattended, the durable options are to add more company boards,
or to point `indeed.base_url` at a licensed feed that returns the same JSON
shape.

## Filtering

```yaml
match:
  title_include: [backend, platform engineer]   # any one must appear
  title_exclude: [intern, manager]              # none may appear
  location_include: [new york, remote]
  location_exclude: [japan]
  company_exclude: []
  description_exclude: [security clearance]
  remote_only: false
  max_age_days: 21
```

Matching is case-insensitive **substring** matching, not whole-word: `engineer`
also matches `Engineering Manager`. Narrow with `title_exclude` rather than
expecting word boundaries.

`title_include` keywords are OR-ed. Nest them to build groups that are AND-ed,
for when two things must both hold:

```yaml
title_include:
  - [quantitative, actuarial, data scien]   # the role, and
  - [intern, co-op, summer analyst]         # an internship
```

That matches `Actuarial Analyst Co-op` but rejects both `Actuarial Analyst`
(not an internship) and `Software Engineer Intern` (wrong field).

Two deliberate behaviours worth knowing:

- A **remote** role satisfies `location_include` regardless of the city named,
  since it is workable from anywhere. Set `remote_satisfies_location: false`
  when your search is tied to one country and `Remote - US` is not a role you
  could take.
- A posting with **no date** survives `max_age_days`; some boards omit
  timestamps, and dropping those would hide real matches.

Use `jobsearch run --explain` to see the reason each posting was filtered out.

## How it avoids repeat alerts

Every posting gets a fingerprint — the source's own job id where there is one,
otherwise its URL with tracking parameters stripped. Fingerprints live in
`state/seen.json`, which the workflow commits after each run. Consequences
worth knowing:

- **The first run sends nothing.** Every posting on every board would look new,
  so the first run records them as a baseline and alerts from then on. Set
  `notify_on_first_run: true` if you would rather get the initial dump.
- **If Discord fails, nothing is recorded**, so the next run retries those jobs
  rather than silently swallowing them.
- **Postings held back by `max_per_run` are not recorded**, so they lead the
  next digest rather than being silently dropped.
- **Deleting `state/seen.json` re-seeds** rather than re-sending everything.
- Entries older than 180 days are pruned to keep the file small.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Tests run entirely against recorded fixtures in `tests/fixtures/` — no network,
so parser changes are verifiable without hitting a live board.

### Adding a source

Subclass `Source`, set `type_name`, and yield `Job`s from `fetch()`:

```python
from jobsearch.sources.base import Source, register
from jobsearch.models import Job

@register
class MyBoardSource(Source):
    type_name = "myboard"
    description = "My board (option: companies: [...])"

    def fetch(self):
        for item in self.client.get_json("https://api.example.com/jobs"):
            yield Job(source=self.type_name, company=item["org"],
                      title=item["title"], url=item["url"],
                      location=item.get("city", ""), source_id=str(item["id"]))
```

Import it in `jobsearch/sources/__init__.py` and it becomes available as
`type: myboard` in config. Notifiers work the same way via
`jobsearch/notifiers/base.py` — implement `send(jobs)` to add email, Slack or
anything else alongside Discord.
