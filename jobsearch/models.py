"""Core data types shared by sources, matching, storage and notifiers."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

# Strictly tracking/pagination noise. Anything that identifies *which* job a
# URL points at must stay: `gh_jid` is the Greenhouse job id and is the only
# identity in a posting hosted on a company's own domain, and `vjk`/`jk` are
# Indeed's job keys. Stripping those yields a link to a generic careers page.
_TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z]+|gh_src|lever-source|source|ref|refId|trackingId|trk|"
    r"originalSubdomain|pageNum|eBP|from)$",
    re.IGNORECASE,
)


def canonical_url(url: str) -> str:
    """Strip tracking noise so the same posting hashes to one fingerprint.

    Job boards decorate apply links with per-visit parameters; without this the
    same posting looks new on every run.
    """
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    if not url:
        return ""
    parts = urlsplit(url.strip())
    kept = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept), ""))


@dataclass
class Job:
    """A single job posting, normalised across every source."""

    source: str
    company: str
    title: str
    url: str
    location: str = ""
    remote: bool | None = None
    department: str = ""
    posted_at: datetime | None = None
    description: str = ""
    source_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.company = (self.company or "").strip()
        self.title = (self.title or "").strip()
        self.location = (self.location or "").strip()
        self.url = (self.url or "").strip()

    @property
    def apply_url(self) -> str:
        """The link to hand to the user — the canonical posting URL."""
        return canonical_url(self.url)

    @property
    def fingerprint(self) -> str:
        """Stable identity used to decide whether a posting has been seen.

        Prefers the source's own id (survives URL churn) and falls back to the
        canonical URL, then to company+title.
        """
        if self.source_id:
            basis = f"{self.source}:{self.source_id}"
        elif self.url:
            basis = f"{self.source}:{canonical_url(self.url)}"
        else:
            basis = f"{self.source}:{self.company.lower()}:{self.title.lower()}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]

    def age_days(self, now: datetime | None = None) -> float | None:
        if self.posted_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        posted = self.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return (now - posted).total_seconds() / 86400.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        data["url"] = self.apply_url
        return data
