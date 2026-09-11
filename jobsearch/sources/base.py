"""Source plugin contract and registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Iterable, Type

from ..http import HttpClient
from ..models import Job

log = logging.getLogger(__name__)

_REGISTRY: dict[str, Type["Source"]] = {}


def register(cls: Type["Source"]) -> Type["Source"]:
    """Class decorator that makes a source addressable by `type:` in config."""
    _REGISTRY[cls.type_name] = cls
    return cls


def get_source(type_name: str) -> Type["Source"]:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(
            f"unknown source type {type_name!r}. Available: {', '.join(sorted(_REGISTRY))}"
        ) from None


def available_sources() -> dict[str, Type["Source"]]:
    return dict(sorted(_REGISTRY.items()))


class Source(ABC):
    """Fetches postings from one provider and normalises them into `Job`s."""

    #: value used in config.yaml under `type:`
    type_name: str = ""
    #: shown by `jobsearch sources`
    description: str = ""
    #: True for sources that scrape sites whose terms prohibit it
    requires_opt_in: bool = False

    def __init__(self, options: dict[str, Any], client: HttpClient) -> None:
        self.options = options
        self.client = client

    @abstractmethod
    def fetch(self) -> Iterable[Job]:
        """Yield every posting this source currently offers.

        Filtering happens later in `matching`; a source's job is retrieval and
        normalisation only.
        """

    def _require(self, key: str) -> Any:
        if key not in self.options:
            raise KeyError(f"source {self.type_name!r} requires option {key!r} in config.yaml")
        return self.options[key]
