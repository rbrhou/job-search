"""Indeed search results.

Opt-in source. Indeed retired its public Publisher API, its terms prohibit
scraping, and the search pages sit behind Cloudflare — a GitHub Actions runner
will almost always get a challenge page rather than results. Two ways to use it:

1. `base_url` unset: parse indeed.com search pages directly. Best-effort only.
2. `base_url` set to a licensed provider that returns the same mosaic JSON
   shape (or an Indeed partner feed). This is the path that actually works
   unattended.

Either way the source degrades to a warning instead of failing the run.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlencode, urljoin

from ..http import FetchError
from ..models import Job
from .base import Source, register

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://www.indeed.com"
PAGE_SIZE = 10

_MOSAIC_RE = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.*?\});',
    re.DOTALL,
)


@register
class IndeedSource(Source):
    type_name = "indeed"
    description = (
        "Indeed search (opt-in; scraping breaches Indeed's terms and is Cloudflare-"
        "blocked from CI — point `base_url` at a licensed feed for reliable results)"
    )
    requires_opt_in = True

    def fetch(self) -> Iterable[Job]:
        queries = self._require("queries")
        max_pages = int(self.options.get("max_pages", 2))
        base_url = self.options.get("base_url", DEFAULT_BASE).rstrip("/")
        posted_within_days = self.options.get("posted_within_days")

        for query in queries:
            what = query.get("what", "") if isinstance(query, dict) else str(query)
            where = query.get("where", "") if isinstance(query, dict) else ""
            try:
                yield from self._search(base_url, what, where, max_pages, posted_within_days)
            except FetchError as exc:
                log.warning(
                    "indeed: query %r blocked or unavailable (%s). This source is "
                    "best-effort; the run continues.",
                    what,
                    exc,
                )

    def _search(
        self,
        base_url: str,
        what: str,
        where: str,
        max_pages: int,
        posted_within_days: int | None,
    ) -> Iterable[Job]:
        for page in range(max_pages):
            params: dict[str, Any] = {"q": what, "l": where, "start": page * PAGE_SIZE}
            if posted_within_days:
                params["fromage"] = posted_within_days
            html = self.client.get(f"{base_url}/jobs?{urlencode(params)}").text
            jobs = list(self.parse(html, base_url))
            log.info("indeed: %r page %d returned %d postings", what, page + 1, len(jobs))
            if not jobs:
                if page == 0 and "cloudflare" in html.lower():
                    raise FetchError("Indeed served a bot challenge instead of results")
                break
            yield from jobs

    def parse(self, html: str, base_url: str = DEFAULT_BASE) -> Iterable[Job]:
        """Extract postings from the JSON blob Indeed embeds in its search page."""
        match = _MOSAIC_RE.search(html)
        if not match:
            return
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            log.warning("indeed: embedded job JSON did not parse: %s", exc)
            return
        results = (
            payload.get("metaData", {})
            .get("mosaicProviderJobCardsModel", {})
            .get("results", [])
        )
        for item in results:
            job = self._to_job(item, base_url)
            if job:
                yield job

    def _to_job(self, item: dict[str, Any], base_url: str) -> Job | None:
        title = item.get("title") or item.get("displayTitle")
        job_key = item.get("jobkey")
        if not title or not job_key:
            return None
        posted_at = None
        # Indeed reports epoch milliseconds under a couple of different keys.
        for key in ("pubDate", "createDate"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                posted_at = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
                break
        return Job(
            source=self.type_name,
            company=item.get("company") or "",
            title=title,
            url=urljoin(base_url + "/", f"viewjob?jk={job_key}"),
            location=item.get("formattedLocation") or item.get("jobLocationCity") or "",
            remote=bool(item.get("remoteLocation")) or None,
            posted_at=posted_at,
            description=(item.get("snippet") or "")[:4000],
            source_id=str(job_key),
        )
