"""Decide which postings are worth a notification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import MatchConfig
from .models import Job

_REMOTE_HINTS = ("remote", "anywhere", "distributed", "work from home", "wfh")


@dataclass
class MatchResult:
    matched: bool
    reason: str = ""


def _contains_any(haystack: str, needles: list[str]) -> str | None:
    """Return the first needle found in haystack, or None."""
    lowered = haystack.lower()
    for needle in needles:
        if needle and needle in lowered:
            return needle
    return None


def looks_remote(job: Job) -> bool:
    if job.remote is not None:
        return job.remote
    blob = f"{job.title} {job.location}".lower()
    return any(hint in blob for hint in _REMOTE_HINTS)


def evaluate(job: Job, match: MatchConfig, now: datetime | None = None) -> MatchResult:
    """Apply every configured filter, reporting why a posting was dropped."""
    now = now or datetime.now(timezone.utc)

    # Every group must hit: groups are AND-ed, keywords within one are OR-ed.
    for group in match.title_include:
        if not _contains_any(job.title, group):
            shown = ", ".join(group[:4]) + ("…" if len(group) > 4 else "")
            return MatchResult(False, f"title matches none of [{shown}]")

    hit = _contains_any(job.title, match.title_exclude)
    if hit:
        return MatchResult(False, f"title contains excluded keyword {hit!r}")

    hit = _contains_any(job.company, match.company_exclude)
    if hit:
        return MatchResult(False, f"company excluded by {hit!r}")

    hit = _contains_any(job.location, match.location_exclude)
    if hit:
        return MatchResult(False, f"location contains excluded keyword {hit!r}")

    if match.description_exclude:
        hit = _contains_any(job.description, match.description_exclude)
        if hit:
            return MatchResult(False, f"description contains excluded keyword {hit!r}")

    if match.remote_only and not looks_remote(job):
        return MatchResult(False, "not a remote role")

    if match.location_include:
        # A remote role normally satisfies a location filter, being workable from
        # anywhere — unless the search is tied to one country, where "Remote - US"
        # is not a role you can take.
        remote_counts = match.remote_satisfies_location and looks_remote(job)
        if not _contains_any(job.location, match.location_include) and not remote_counts:
            return MatchResult(False, "location matches no location_include keyword")

    if match.max_age_days is not None:
        age = job.age_days(now)
        if age is not None and age > match.max_age_days:
            return MatchResult(False, f"posted {age:.0f} days ago (max {match.max_age_days})")

    return MatchResult(True, "matched")


def filter_jobs(jobs: list[Job], match: MatchConfig, now: datetime | None = None) -> list[Job]:
    return [job for job in jobs if evaluate(job, match, now).matched]
