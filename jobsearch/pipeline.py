"""The run loop: fetch every source, filter, drop duplicates, notify, remember."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config
from .http import HttpClient
from .matching import evaluate
from .models import Job
from .notifiers import get_notifier
from .sources import get_source
from .store import SeenStore

log = logging.getLogger(__name__)


class OptInRequired(RuntimeError):
    """A source whose terms need explicit acknowledgement was left unacknowledged."""


@dataclass
class RunReport:
    fetched: int = 0
    matched: int = 0
    new: int = 0
    notified: int = 0
    seeded: int = 0
    per_source: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"fetched {self.fetched}",
            f"matched {self.matched}",
            f"new {self.new}",
            f"notified {self.notified}",
        ]
        if self.seeded:
            parts.append(f"seeded {self.seeded} (first run, no alerts sent)")
        if self.errors:
            parts.append(f"{len(self.errors)} source error(s)")
        return ", ".join(parts)


def collect(config: Config, client: HttpClient, report: RunReport) -> list[Job]:
    """Fetch from every enabled source, isolating failures to that source."""
    jobs: list[Job] = []
    for source_config in config.enabled_sources:
        source_cls = get_source(source_config.type)
        if source_cls.requires_opt_in and not source_config.options.get("accept_terms_risk"):
            raise OptInRequired(
                f"source {source_config.type!r} scrapes a site whose terms prohibit it and "
                f"which blocks automated traffic. Set `accept_terms_risk: true` on that "
                f"source in config.yaml to enable it anyway, or remove the source."
            )
        source = source_cls(source_config.options, client)
        try:
            found = list(source.fetch())
        except Exception as exc:  # a broken source must not sink the whole run
            log.warning("source %s failed: %s", source_config.type, exc)
            report.errors.append(f"{source_config.type}: {exc}")
            continue
        report.per_source[source_config.type] = report.per_source.get(source_config.type, 0) + len(found)
        jobs.extend(found)
    report.fetched = len(jobs)
    return jobs


def run(config: Config, dry_run: bool = False, explain: bool = False) -> RunReport:
    """One full pass. `dry_run` skips both notification and state writes."""
    report = RunReport()
    client = HttpClient(user_agent=config.user_agent, timeout=config.request_timeout)
    try:
        jobs = collect(config, client, report)

        matched: list[Job] = []
        for job in jobs:
            result = evaluate(job, config.match)
            if result.matched:
                matched.append(job)
            elif explain:
                log.info("filtered out %s — %s (%s)", job.title, job.company, result.reason)
        report.matched = len(matched)

        store = SeenStore(config.state_path)
        seeding = len(store) == 0 and not config.notify_on_first_run
        fresh = store.filter_new(matched)
        report.new = len(fresh)

        if seeding and fresh:
            # First run: every posting on every board looks new. Sending all of
            # them would bury the channel, so record them and start alerting on
            # what appears from here on. `notify_on_first_run: true` opts out.
            log.info(
                "first run: recording %d existing posting(s) as the baseline without "
                "notifying. Future runs alert on anything new.",
                len(fresh),
            )
            report.seeded = len(fresh)
            if not dry_run:
                store.record(matched)
                store.save()
            else:
                _preview(fresh)
            return report

        # Newest first, so a truncated digest keeps the most relevant postings.
        fresh.sort(key=lambda j: (j.posted_at is not None, j.posted_at), reverse=True)
        if len(fresh) > config.max_per_run:
            log.info(
                "capping digest at %d of %d new postings; the rest are recorded as seen "
                "and will not be re-sent",
                config.max_per_run,
                len(fresh),
            )
            fresh = fresh[: config.max_per_run]

        if dry_run:
            log.info("dry run: not notifying, not writing state")
            report.notified = 0
            _preview(fresh)
            return report

        if fresh:
            for notifier_config in config.enabled_notifiers:
                notifier = get_notifier(notifier_config.type)(notifier_config.options, client)
                try:
                    notifier.send(fresh)
                    report.notified = len(fresh)
                except Exception as exc:
                    log.error("notifier %s failed: %s", notifier_config.type, exc)
                    report.errors.append(f"{notifier_config.type}: {exc}")
                    # Do not mark these as seen — a later run should retry them.
                    return report
        else:
            log.info("no new postings this run")

        store.record(matched)
        pruned = store.prune()
        if pruned:
            log.info("pruned %d stale state entries", pruned)
        store.save()
        return report
    finally:
        client.close()


def _preview(jobs: list[Job]) -> None:
    for job in jobs:
        print(f"{job.company} — {job.title} [{job.location or 'n/a'}] {job.apply_url}")
