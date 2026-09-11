"""Ashby public job board API.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from ..http import FetchError
from ..models import Job
from .base import Source, register
from .greenhouse import parse_timestamp

log = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board/{board}"


@register
class AshbySource(Source):
    type_name = "ashby"
    description = "Ashby public job board API (option: boards: [acme, ...])"

    def fetch(self) -> Iterable[Job]:
        boards = self._require("boards")
        if isinstance(boards, str):
            boards = [boards]
        for board in boards:
            try:
                yield from self._fetch_board(str(board))
            except FetchError as exc:
                log.warning("ashby: skipping board %r: %s", board, exc)

    def _fetch_board(self, board: str) -> Iterable[Job]:
        payload = self.client.get_json(
            API.format(board=board), params={"includeCompensation": "true"}
        )
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        log.info("ashby: %s returned %d postings", board, len(jobs))
        for item in jobs:
            job = self._to_job(board, item)
            if job:
                yield job

    def _to_job(self, board: str, item: dict[str, Any]) -> Job | None:
        title = item.get("title")
        url = item.get("jobUrl") or item.get("applyUrl")
        if not title or not url:
            return None
        return Job(
            source=self.type_name,
            company=item.get("companyName") or board,
            title=title,
            url=url,
            location=item.get("location") or "",
            department=item.get("department") or item.get("team") or "",
            remote=item.get("isRemote"),
            posted_at=parse_timestamp(item.get("publishedAt") or item.get("updatedAt")),
            description=(item.get("descriptionPlain") or "")[:4000],
            source_id=f"{board}:{item.get('id')}",
            extra={"compensation": item.get("compensation")} if item.get("compensation") else {},
        )
