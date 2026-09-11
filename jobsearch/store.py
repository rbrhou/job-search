"""Remembering which postings have already been sent.

A JSON file rather than SQLite: the GitHub Actions workflow commits this back to
the repo after every run, and JSON produces a readable diff.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Job

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class SeenStore:
    """Fingerprint -> when we first saw it, pruned after `retention_days`."""

    def __init__(self, path: str | Path, retention_days: int = 180) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self.entries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text() or "{}")
        except json.JSONDecodeError as exc:
            # A corrupt state file must not block alerts; worst case is a repeat.
            log.warning("state file %s is unreadable (%s); starting fresh", self.path, exc)
            return
        if raw.get("version") != SCHEMA_VERSION:
            log.warning("state file %s has unknown version; starting fresh", self.path)
            return
        self.entries = raw.get("jobs", {})

    def is_new(self, job: Job) -> bool:
        return job.fingerprint not in self.entries

    def filter_new(self, jobs: list[Job]) -> list[Job]:
        """Drop already-seen postings, and de-duplicate within this batch.

        The same role often appears on both a company board and an aggregator.
        """
        out: list[Job] = []
        batch: set[str] = set()
        for job in jobs:
            fp = job.fingerprint
            if fp in batch or not self.is_new(job):
                continue
            batch.add(fp)
            out.append(job)
        return out

    def record(self, jobs: list[Job], now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        for job in jobs:
            self.entries.setdefault(
                job.fingerprint,
                {
                    "first_seen": stamp,
                    "source": job.source,
                    "company": job.company,
                    "title": job.title,
                    "url": job.apply_url,
                },
            )

    def prune(self, now: datetime | None = None) -> int:
        """Forget entries older than the retention window, keeping state small."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.retention_days)
        stale = []
        for fp, entry in self.entries.items():
            try:
                first_seen = datetime.fromisoformat(entry["first_seen"])
            except (KeyError, ValueError):
                continue
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=timezone.utc)
            if first_seen < cutoff:
                stale.append(fp)
        for fp in stale:
            del self.entries[fp]
        return len(stale)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "jobs": dict(sorted(self.entries.items())),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
        tmp.replace(self.path)

    def __len__(self) -> int:
        return len(self.entries)
