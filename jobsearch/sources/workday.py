"""Workday career-site API.

Every Workday-hosted careers page is driven by a JSON endpoint that the page
itself calls:

    POST https://{host}/wday/cxs/{tenant}/{site}/jobs
    {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

This is the public, unauthenticated feed behind a company's own job search — not
a scrape — which matters because Canadian banks, insurers and pension funds run
almost entirely on Workday. Without this source, actuarial and quant roles are
effectively unreachable.

Configure an employer with the career-site URL you'd visit in a browser; the
tenant, host and site name are derived from it:

    - type: workday
      employers:
        - name: Sun Life
          url: https://sunlife.wd3.myworkdayjobs.com/en-US/Experienced-Jobs
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..http import FetchError
from ..models import Job
from .base import Source, register

log = logging.getLogger(__name__)

#: Workday caps a page at 20 regardless of what `limit` asks for.
PAGE_SIZE = 20

_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_POD_HOST = re.compile(r"^(?P<tenant>[^.]+)\.(?P<pod>wd\d+)\.myworkdayjobs\.com$")

#: Workday shards tenants across numbered pods. `*.wdN.myworkdayjobs.com` has
#: wildcard DNS, so pointing at the wrong pod resolves and then fails at the
#: application layer rather than in DNS — which is why a tenant on the wrong pod
#: answers 422 while a wrong site name on the right pod answers 404. Probing the
#: other pods recovers the first case without anyone looking a URL up by hand.
POD_CANDIDATES = ("wd1", "wd2", "wd3", "wd5", "wd10", "wd12")
_RELATIVE_POSTED = re.compile(r"(\d+)\+?\s*(day|days|hour|hours|month|months)\s*ago", re.IGNORECASE)


class WorkdayTargetError(ValueError):
    """An employer entry that cannot be resolved to a Workday endpoint."""


def parse_target(entry: Any) -> tuple[str, str, str]:
    """Resolve an employer config entry to (host, tenant, site).

    Accepts a career-site URL, a bare string URL, or explicit host/tenant/site.
    """
    if isinstance(entry, str):
        entry = {"url": entry}
    if not isinstance(entry, dict):
        raise WorkdayTargetError(f"expected a mapping or URL string, got {entry!r}")

    if "url" in entry:
        parts = urlsplit(entry["url"])
        if not parts.netloc:
            raise WorkdayTargetError(f"{entry['url']!r} is not an absolute URL")
        host = parts.netloc
        segments = [s for s in parts.path.split("/") if s]
        # Career-site URLs carry an optional locale segment: /en-US/SiteName
        segments = [s for s in segments if not _LOCALE.match(s)]
        if not segments:
            raise WorkdayTargetError(
                f"{entry['url']!r} has no site name — expected .../en-US/<SiteName>"
            )
        return host, entry.get("tenant") or host.split(".")[0], segments[-1]

    missing = [k for k in ("host", "tenant", "site") if k not in entry]
    if missing:
        raise WorkdayTargetError(
            f"employer entry needs either 'url' or all of host/tenant/site "
            f"(missing {', '.join(missing)})"
        )
    return entry["host"], entry["tenant"], entry["site"]


def parse_posted(text: str | None, now: datetime | None = None) -> datetime | None:
    """Workday reports posting age as prose: "Posted 3 Days Ago"."""
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    lowered = text.lower()
    if "today" in lowered:
        return now
    if "yesterday" in lowered:
        return now - timedelta(days=1)
    match = _RELATIVE_POSTED.search(lowered)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).rstrip("s")
    deltas = {
        "hour": timedelta(hours=amount),
        "day": timedelta(days=amount),
        "month": timedelta(days=30 * amount),
    }
    return now - deltas[unit]


def host_variants(host: str) -> list[str]:
    """The configured host first, then the same tenant on the other pods."""
    match = _POD_HOST.match(host)
    if not match:
        return [host]
    tenant, pod = match.group("tenant"), match.group("pod")
    pods = [pod] + [p for p in POD_CANDIDATES if p != pod]
    return [f"{tenant}.{p}.myworkdayjobs.com" for p in pods]


@register
class WorkdaySource(Source):
    type_name = "workday"
    description = "Workday career-site API (option: employers: [{name, url}, ...])"

    def fetch(self) -> Iterable[Job]:
        employers = self._require("employers")
        for entry in employers:
            try:
                host, tenant, site = parse_target(entry)
            except WorkdayTargetError as exc:
                log.warning("workday: skipping malformed employer entry: %s", exc)
                continue
            name = entry.get("name", tenant) if isinstance(entry, dict) else tenant
            try:
                host = self._resolve_host(entry, host, tenant, site, name)
                yield from self._fetch_employer(entry, host, tenant, site, name)
            except FetchError as exc:
                # A wrong site name 404s; one bad employer must not sink the run.
                log.warning("workday: skipping %s (%s/%s): %s", name, tenant, site, exc)

    def _resolve_host(
        self, entry: Any, host: str, tenant: str, site: str, name: str
    ) -> str:
        """Find a pod that actually serves this tenant, with one cheap probe.

        Returns the configured host untouched when it works, so the common case
        costs a single extra request.
        """
        opts = entry if isinstance(entry, dict) else {}
        allow_fallback = opts.get("pod_fallback", self.options.get("pod_fallback", True))
        candidates = host_variants(host) if allow_fallback else [host]
        last_error: Exception | None = None

        for candidate in candidates:
            url = f"https://{candidate}/wday/cxs/{tenant}/{site}/jobs"
            probe = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
            try:
                self.client.post_json(url, probe, headers={"Referer": f"https://{candidate}"})
            except FetchError as exc:
                last_error = exc
                continue
            if candidate != host:
                log.info(
                    "workday: %s is served by %s, not the configured host — pin "
                    "https://%s/en-US/%s in config.yaml to skip this probe",
                    name, candidate, candidate, site,
                )
            return candidate

        raise FetchError(
            f"no Workday pod served {tenant}/{site}; a 404 means the site name is "
            f"wrong, a 422 that the tenant is elsewhere (last: {last_error})"
        )

    def _search_terms(self, opts: dict[str, Any]) -> list[str]:
        """Which server-side searches to run for one employer.

        Large employers carry thousands of open roles, far past any sane page
        limit, so narrowing server-side beats paginating the whole board. Several
        terms are needed because one query cannot span the vocabulary — a
        Canadian internship is variously an "intern", a "co-op" or a "student".
        Duplicates across terms collapse later on fingerprint.
        """
        terms = opts.get("search_texts") or self.options.get("search_texts")
        if terms:
            return [str(t) for t in terms]
        return [opts.get("search_text", self.options.get("search_text", ""))]

    def _fetch_employer(
        self, entry: Any, host: str, tenant: str, site: str, name: str
    ) -> Iterable[Job]:
        opts = entry if isinstance(entry, dict) else {}
        max_pages = int(opts.get("max_pages", self.options.get("max_pages", 10)))
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        base = f"https://{host}"
        total_seen = 0

        for term in self._search_terms(opts):
            seen = 0
            for page in range(max_pages):
                payload = {
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": page * PAGE_SIZE,
                    "searchText": term,
                }
                data = self.client.post_json(url, payload, headers={"Referer": base})
                postings = data.get("jobPostings") or [] if isinstance(data, dict) else []
                reported = data.get("total", 0) if isinstance(data, dict) else 0
                for item in postings:
                    job = self._to_job(item, base, site, tenant, name)
                    if job:
                        yield job
                seen += len(postings)
                if not postings or seen >= reported:
                    break
            total_seen += seen
        log.info("workday: %s returned %d postings", name, total_seen)

    def _to_job(
        self, item: dict[str, Any], base: str, site: str, tenant: str, name: str
    ) -> Job | None:
        title = item.get("title")
        path = item.get("externalPath")
        if not title or not path:
            return None
        location = item.get("locationsText") or ""
        # Multi-location postings report "3 Locations" rather than naming them,
        # which no location filter can match on. Keep the bullet fields, which
        # usually carry the requisition id, and let the title/company carry it.
        return Job(
            source=self.type_name,
            company=name,
            title=title,
            url=f"{base}/en-US/{site}{path}",
            location=location,
            remote=True if "remote" in location.lower() else None,
            posted_at=parse_posted(item.get("postedOn")),
            source_id=f"{tenant}:{path}",
            extra={"requisition": item.get("bulletFields") or []},
        )
