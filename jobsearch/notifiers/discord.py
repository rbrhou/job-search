"""Discord incoming-webhook notifier.

Posts one embed per job, batched to Discord's limits, with explicit handling of
the webhook rate limit (HTTP 429 + retry_after).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..models import Job
from .base import Notifier, register

log = logging.getLogger(__name__)

#: Discord's documented ceilings for a webhook execute payload.
MAX_EMBEDS_PER_MESSAGE = 10
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELD_VALUE = 1024
MAX_TOTAL_EMBED_CHARS = 6000

#: A stable accent colour per source, so the channel is skimmable.
SOURCE_COLORS = {
    "greenhouse": 0x24A47F,
    "lever": 0x4A7DFF,
    "ashby": 0x6E56CF,
    "workable": 0x00A4A6,
    "linkedin": 0x0A66C2,
    "indeed": 0x2557A7,
}
DEFAULT_COLOR = 0x5865F2


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class DiscordError(RuntimeError):
    """The webhook rejected a message."""


@register
class DiscordNotifier(Notifier):
    type_name = "discord"
    description = "Post new postings to a Discord channel (option: webhook_url)"

    def send(self, jobs: list[Job]) -> None:
        webhook_url = self._require("webhook_url")
        username = self.options.get("username", "Job Search")
        mention = self.options.get("mention", "")

        embeds = [self.build_embed(job) for job in jobs]
        batches = list(self._batch(embeds))
        log.info("discord: sending %d job(s) in %d message(s)", len(jobs), len(batches))

        for index, batch in enumerate(batches):
            payload: dict[str, Any] = {"username": username, "embeds": batch}
            if index == 0:
                header = f"**{len(jobs)} new job posting{'s' if len(jobs) != 1 else ''}**"
                payload["content"] = f"{mention} {header}".strip()
                # Only ping for the first message of a digest, never per batch.
                payload["allowed_mentions"] = {"parse": ["roles", "users"] if mention else []}
            self._post(webhook_url, payload)

    def build_embed(self, job: Job) -> dict[str, Any]:
        """Render one posting as a Discord embed."""
        fields = []
        if job.location:
            fields.append(
                {"name": "Location", "value": _truncate(job.location, MAX_FIELD_VALUE), "inline": True}
            )
        if job.department:
            fields.append(
                {"name": "Team", "value": _truncate(job.department, MAX_FIELD_VALUE), "inline": True}
            )
        if job.posted_at:
            # Discord renders <t:epoch:R> as a live "3 days ago".
            fields.append(
                {
                    "name": "Posted",
                    "value": f"<t:{int(job.posted_at.timestamp())}:R>",
                    "inline": True,
                }
            )

        embed: dict[str, Any] = {
            "title": _truncate(f"{job.title}", MAX_TITLE),
            "url": job.apply_url,
            "color": SOURCE_COLORS.get(job.source, DEFAULT_COLOR),
            "author": {"name": _truncate(job.company or job.source, MAX_TITLE)},
            "footer": {"text": f"via {job.source}"},
        }
        if fields:
            embed["fields"] = fields
        if job.description:
            embed["description"] = _truncate(job.description, 300)
        return embed

    def _batch(self, embeds: list[dict[str, Any]]):
        """Split embeds into messages within Discord's count and size limits."""
        batch: list[dict[str, Any]] = []
        chars = 0
        for embed in embeds:
            size = _embed_chars(embed)
            over_count = len(batch) >= MAX_EMBEDS_PER_MESSAGE
            over_chars = batch and chars + size > MAX_TOTAL_EMBED_CHARS
            if over_count or over_chars:
                yield batch
                batch, chars = [], 0
            batch.append(embed)
            chars += size
        if batch:
            yield batch

    def _post(self, webhook_url: str, payload: dict[str, Any], attempt: int = 0) -> None:
        response = self.client.post(webhook_url, json=payload)
        if response.status_code == 429 and attempt < 5:
            # Discord tells us exactly how long to wait; honour it rather than guess.
            retry_after = _retry_after(response)
            log.info("discord: rate limited, retrying in %.1fs", retry_after)
            time.sleep(retry_after)
            self._post(webhook_url, payload, attempt + 1)
            return
        if response.status_code >= 400:
            raise DiscordError(
                f"Discord webhook returned HTTP {response.status_code}: {response.text[:300]}"
            )


def _retry_after(response) -> float:
    try:
        return float(response.json().get("retry_after", 1.0))
    except (ValueError, AttributeError):
        return float(response.headers.get("Retry-After", 1.0))


def _embed_chars(embed: dict[str, Any]) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed.get("author", {}).get("name", ""))
    total += len(embed.get("footer", {}).get("text", ""))
    for field in embed.get("fields", []):
        total += len(field.get("name", "")) + len(field.get("value", ""))
    return total
