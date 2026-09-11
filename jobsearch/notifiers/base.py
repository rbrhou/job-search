"""Notifier plugin contract and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Type

from ..http import HttpClient
from ..models import Job

_REGISTRY: dict[str, Type["Notifier"]] = {}


def register(cls: Type["Notifier"]) -> Type["Notifier"]:
    _REGISTRY[cls.type_name] = cls
    return cls


def get_notifier(type_name: str) -> Type["Notifier"]:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(
            f"unknown notifier type {type_name!r}. Available: {', '.join(sorted(_REGISTRY))}"
        ) from None


def available_notifiers() -> dict[str, Type["Notifier"]]:
    return dict(sorted(_REGISTRY.items()))


class Notifier(ABC):
    type_name: str = ""
    description: str = ""

    def __init__(self, options: dict[str, Any], client: HttpClient) -> None:
        self.options = options
        self.client = client

    @abstractmethod
    def send(self, jobs: list[Job]) -> None:
        """Deliver the new postings. Called only when `jobs` is non-empty."""

    def _require(self, key: str) -> Any:
        if key not in self.options:
            raise KeyError(f"notifier {self.type_name!r} requires option {key!r} in config.yaml")
        return self.options[key]
