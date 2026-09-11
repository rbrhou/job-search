"""Lever public postings API.

Endpoint: https://api.lever.co/v0/postings/{company}?mode=json
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from ..http import FetchError
from ..models import Job
from .base import Source, register

log = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings/{company}"


@register
class LeverSource(Source):
    type_name = "lever"
    description = "Lever public postings API (option: companies: [acme, ...])"

    def fetch(self) -> Iterable[Job]:
        companies = self._require("companies")
        if isinstance(companies, str):
            companies = [companies]
        for company in companies:
            try:
                yield from self._fetch_company(str(company))
            except FetchError as exc:
                log.warning("lever: skipping company %r: %s", company, exc)

    def _fetch_company(self, company: str) -> Iterable[Job]:
        payload = self.client.get_json(API.format(company=company), params={"mode": "json"})
        if not isinstance(payload, list):
            log.warning("lever: %s returned an unexpected payload", company)
            return
        log.info("lever: %s returned %d postings", company, len(payload))
        for item in payload:
            job = self._to_job(company, item)
            if job:
                yield job

    def _to_job(self, company: str, item: dict[str, Any]) -> Job | None:
        title = item.get("text")
        url = item.get("hostedUrl") or item.get("applyUrl")
        if not title or not url:
            return None
        categories = item.get("categories") or {}
        posted_at = None
        if isinstance(item.get("createdAt"), (int, float)):
            # Lever reports epoch milliseconds.
            posted_at = datetime.fromtimestamp(item["createdAt"] / 1000, tz=timezone.utc)
        workplace = (item.get("workplaceType") or "").lower()
        return Job(
            source=self.type_name,
            company=company,
            title=title,
            url=url,
            location=categories.get("location", "") or "",
            department=categories.get("team", "") or categories.get("department", "") or "",
            remote=True if workplace == "remote" else (False if workplace else None),
            posted_at=posted_at,
            description=(item.get("descriptionPlain") or "")[:4000],
            source_id=f"{company}:{item.get('id')}",
        )
