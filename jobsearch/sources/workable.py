"""Workable public job board API.

Endpoint: https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..http import FetchError
from ..models import Job
from .base import Source, register
from .greenhouse import parse_timestamp

log = logging.getLogger(__name__)

API = "https://apply.workable.com/api/v1/widget/accounts/{account}"


@register
class WorkableSource(Source):
    type_name = "workable"
    description = "Workable public widget API (option: accounts: [acme, ...])"

    def fetch(self) -> Iterable[Job]:
        accounts = self._require("accounts")
        if isinstance(accounts, str):
            accounts = [accounts]
        for account in accounts:
            try:
                yield from self._fetch_account(str(account))
            except FetchError as exc:
                log.warning("workable: skipping account %r: %s", account, exc)

    def _fetch_account(self, account: str) -> Iterable[Job]:
        payload = self.client.get_json(
            API.format(account=account), params={"details": "true"}
        )
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        company = (payload.get("name") or account) if isinstance(payload, dict) else account
        log.info("workable: %s returned %d postings", account, len(jobs))
        for item in jobs:
            job = self._to_job(account, company, item)
            if job:
                yield job

    def _to_job(self, account: str, company: str, item: dict[str, Any]) -> Job | None:
        title = item.get("title")
        url = item.get("url") or item.get("application_url")
        if not title or not url:
            return None
        location = item.get("location") or {}
        if isinstance(location, dict):
            parts = [location.get("city"), location.get("region"), location.get("country")]
            location_text = ", ".join(p for p in parts if p)
            remote = location.get("workplace") == "remote" or bool(location.get("telecommuting"))
        else:
            location_text, remote = str(location), None
        return Job(
            source=self.type_name,
            company=company,
            title=title,
            url=url,
            location=location_text,
            department=item.get("department") or "",
            remote=remote,
            posted_at=parse_timestamp(item.get("published_on") or item.get("created_at")),
            description=(item.get("description") or "")[:4000],
            source_id=f"{account}:{item.get('shortcode') or item.get('id')}",
        )
