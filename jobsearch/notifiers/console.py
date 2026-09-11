"""Prints matches to stdout. Useful for dry runs and for CI logs."""

from __future__ import annotations

from ..models import Job
from .base import Notifier, register


@register
class ConsoleNotifier(Notifier):
    type_name = "console"
    description = "Print new postings to stdout"

    def send(self, jobs: list[Job]) -> None:
        print(f"\n{len(jobs)} new matching posting(s):\n")
        for job in jobs:
            location = job.location or "location not stated"
            posted = job.posted_at.date().isoformat() if job.posted_at else "date unknown"
            print(f"  {job.company} — {job.title}")
            print(f"    {location} · {posted} · via {job.source}")
            print(f"    {job.apply_url}\n")
