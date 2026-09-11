"""Greenhouse public job board API.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
This is the same JSON that powers a company's public board — documented, stable
and intended for public consumption.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable

from ..http import FetchError
from ..models import Job
from .base import Source, register

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def parse_timestamp(value: str | None) -> datetime | None:
    """Greenhouse/Lever/Ashby all emit ISO-8601, sometimes with a trailing Z."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@register
class GreenhouseSource(Source):
    type_name = "greenhouse"
    description = "Greenhouse public board API (option: boards: [acme, ...])"

    def fetch(self) -> Iterable[Job]:
        boards = self._require("boards")
        if isinstance(boards, str):
            boards = [boards]
        want_content = self.options.get("include_description", True)
        for board in boards:
            try:
                yield from self._fetch_board(str(board), want_content)
            except FetchError as exc:
                # One dead board should not abort the other companies.
                log.warning("greenhouse: skipping board %r: %s", board, exc)

    def _fetch_board(self, board: str, want_content: bool) -> Iterable[Job]:
        params = {"content": "true"} if want_content else {}
        payload = self.client.get_json(API.format(board=board), params=params)
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        log.info("greenhouse: %s returned %d postings", board, len(jobs))
        for item in jobs:
            job = self._to_job(board, item)
            if job:
                yield job

    def _to_job(self, board: str, item: dict[str, Any]) -> Job | None:
        title = item.get("title")
        url = item.get("absolute_url")
        if not title or not url:
            return None
        location = (item.get("location") or {}).get("name", "")
        departments = item.get("departments") or []
        department = departments[0].get("name", "") if departments else ""
        content = item.get("content") or ""
        if content:
            content = _strip_html(content)
        return Job(
            source=self.type_name,
            company=item.get("company_name") or board,
            title=title,
            url=url,
            location=location,
            department=department,
            posted_at=parse_timestamp(item.get("updated_at") or item.get("first_published")),
            description=content[:4000],
            source_id=f"{board}:{item.get('id')}",
        )


def _strip_html(html: str) -> str:
    """Greenhouse returns HTML-escaped markup in `content`; flatten it to text."""
    import html as html_module
    import re

    text = html_module.unescape(html)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
