"""Shared HTTP client: retries, backoff, a polite delay and one user agent."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A source could not be fetched. One source failing must not sink the run."""


class HttpClient:
    """Thin wrapper over requests.Session with retry and rate-limit manners."""

    def __init__(
        self,
        user_agent: str,
        timeout: float = 20.0,
        min_interval: float = 0.6,
        max_retries: int = 3,
    ) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"})
        retry = Retry(
            total=max_retries,
            # Cap connect/read retries. Sources probe speculative hosts (Workday
            # pods, guessed board slugs) where a failure is the expected answer,
            # not a blip worth three more attempts at `timeout` each. Rate limits
            # and 5xx still get the full budget, since those do pass.
            connect=1,
            read=1,
            status=max_retries,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.min_interval - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.2))
        self._last_request = time.monotonic()

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        self._throttle()
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self.session.get(url, **kwargs)
        except requests.RequestException as exc:
            raise FetchError(f"GET {url} failed: {exc}") from exc
        if response.status_code >= 400:
            raise FetchError(f"GET {url} returned HTTP {response.status_code}")
        return response

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"GET {url} did not return JSON: {exc}") from exc

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        self._throttle()
        kwargs.setdefault("timeout", self.timeout)
        try:
            return self.session.post(url, **kwargs)
        except requests.RequestException as exc:
            raise FetchError(f"POST {url} failed: {exc}") from exc

    def post_json(self, url: str, payload: Any, **kwargs: Any) -> Any:
        """POST JSON and parse the JSON reply, raising on an error status.

        Separate from `post`, which returns the raw response without raising —
        the Discord notifier inspects status codes itself to honour rate limits.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(kwargs.pop("headers", {}))
        response = self.post(url, json=payload, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise FetchError(f"POST {url} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"POST {url} did not return JSON: {exc}") from exc

    def close(self) -> None:
        self.session.close()
