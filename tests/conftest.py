"""Shared fixtures: a fake HTTP client that serves recorded payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def load_json_fixture(name: str) -> Any:
    return json.loads(load_fixture(name))


class FakeResponse:
    def __init__(self, body: str = "", status_code: int = 200, json_body: Any = None,
                 headers: dict | None = None) -> None:
        self.text = body
        self.status_code = status_code
        self._json = json_body
        self.headers = headers or {}

    def json(self) -> Any:
        if self._json is None:
            return json.loads(self.text)
        return self._json


class FakeClient:
    """Stands in for HttpClient. Routes by substring match on the URL."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        #: substring -> FakeResponse, a callable, or an Exception to raise
        self.routes = routes or {}
        self.requests: list[tuple[str, dict]] = []
        self.posts: list[tuple[str, dict]] = []

    def _resolve(self, url: str):
        for pattern, value in self.routes.items():
            if pattern in url:
                if isinstance(value, Exception):
                    raise value
                return value(url) if callable(value) else value
        raise AssertionError(f"FakeClient has no route for {url}")

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, kwargs))
        return self._resolve(url)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((url, kwargs))
        try:
            return self._resolve(url)
        except AssertionError:
            return FakeResponse("", 204)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()
