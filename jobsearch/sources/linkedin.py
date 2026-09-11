"""LinkedIn guest job-search endpoint.

Opt-in source. LinkedIn's User Agreement prohibits automated scraping, and the
endpoint is rate-limited and challenge-protected — expect HTTP 429/999 from
datacenter IPs such as GitHub Actions runners. It is off unless you set
`accept_terms_risk: true` in config.yaml, and a run degrades to a warning rather
than failing when LinkedIn blocks it.

Endpoint: /jobs-guest/jobs/api/seeMoreJobPostings/search — the unauthenticated
HTML fragment that backs the public job-search page.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..http import FetchError
from ..models import Job
from .base import Source, register

log = logging.getLogger(__name__)

API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE = 25

#: f_TPR values — LinkedIn's "date posted" filter, in seconds
TIME_FILTERS = {"day": "r86400", "week": "r604800", "month": "r2592000"}
#: f_WT values — on-site / remote / hybrid
WORKPLACE_FILTERS = {"onsite": "1", "remote": "2", "hybrid": "3"}

_JOB_ID_RE = re.compile(r"-(\d{8,})(?:\?|$)")


@register
class LinkedInSource(Source):
    type_name = "linkedin"
    description = (
        "LinkedIn guest search (opt-in; scraping breaches LinkedIn's terms and is "
        "usually blocked from CI IPs)"
    )
    requires_opt_in = True

    def fetch(self) -> Iterable[Job]:
        queries = self._require("queries")
        max_pages = int(self.options.get("max_pages", 2))
        time_filter = self.options.get("posted_within", "week")
        workplace = self.options.get("workplace")

        for query in queries:
            keywords = query.get("keywords", "") if isinstance(query, dict) else str(query)
            location = query.get("location", "") if isinstance(query, dict) else ""
            try:
                yield from self._search(keywords, location, max_pages, time_filter, workplace)
            except FetchError as exc:
                log.warning(
                    "linkedin: query %r blocked or unavailable (%s). This source is "
                    "best-effort; the run continues.",
                    keywords,
                    exc,
                )

    def _search(
        self,
        keywords: str,
        location: str,
        max_pages: int,
        time_filter: str | None,
        workplace: str | None,
    ) -> Iterable[Job]:
        for page in range(max_pages):
            params = {"keywords": keywords, "location": location, "start": page * PAGE_SIZE}
            if time_filter in TIME_FILTERS:
                params["f_TPR"] = TIME_FILTERS[time_filter]
            if workplace in WORKPLACE_FILTERS:
                params["f_WT"] = WORKPLACE_FILTERS[workplace]
            html = self.client.get(f"{API}?{urlencode(params)}").text
            jobs = list(self.parse(html))
            log.info(
                "linkedin: %r page %d returned %d postings", keywords, page + 1, len(jobs)
            )
            yield from jobs
            if len(jobs) < PAGE_SIZE:
                break  # last page

    def parse(self, html: str, now: datetime | None = None) -> Iterable[Job]:
        """Parse the HTML fragment of <li> job cards the guest endpoint returns."""
        now = now or datetime.now(timezone.utc)
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("li"):
            title_el = card.select_one(".base-search-card__title")
            company_el = card.select_one(".base-search-card__subtitle")
            link_el = card.select_one("a.base-card__full-link") or card.select_one("a[href]")
            if not (title_el and link_el and link_el.get("href")):
                continue
            location_el = card.select_one(".job-search-card__location")
            time_el = card.select_one("time")
            url = link_el["href"].split("?")[0]
            match = _JOB_ID_RE.search(link_el["href"])
            yield Job(
                source=self.type_name,
                company=company_el.get_text(strip=True) if company_el else "",
                title=title_el.get_text(strip=True),
                url=url,
                location=location_el.get_text(strip=True) if location_el else "",
                posted_at=_parse_posted(time_el, now),
                source_id=match.group(1) if match else "",
            )


def _parse_posted(time_el, now: datetime) -> datetime | None:
    """LinkedIn gives either a datetime attribute or relative text ("3 days ago")."""
    if time_el is None:
        return None
    stamp = time_el.get("datetime")
    if stamp:
        try:
            return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    text = time_el.get_text(strip=True).lower()
    match = re.match(r"(\d+)\s+(minute|hour|day|week|month)", text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    deltas = {
        "minute": timedelta(minutes=amount),
        "hour": timedelta(hours=amount),
        "day": timedelta(days=amount),
        "week": timedelta(weeks=amount),
        "month": timedelta(days=30 * amount),
    }
    return now - deltas[unit]
