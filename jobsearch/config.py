"""Loading and validating config.yaml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    """Raised when config.yaml is missing required fields or malformed."""


def _expand_env(value: Any) -> Any:
    """Recursively replace ${VAR} with the environment variable's value.

    Secrets (the Discord webhook especially) belong in the environment, never in
    a file that gets committed.
    """
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(
                    f"config references ${{{name}}} but that environment variable is not set"
                )
            return os.environ[name]

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _keyword_groups(value: Any) -> list[list[str]]:
    """Normalise a keyword filter into AND-ed groups of OR-ed keywords.

    A flat list is one group (match any), which is the common case:
        title_include: [backend, platform]

    A list of lists is several groups, and a title must hit every one. That is
    how you say "a quant role AND an internship" rather than "either":
        title_include:
          - [quantitative, data scien]
          - [intern, co-op]
    """
    if not value:
        return []
    if isinstance(value, str):
        return [[value.lower()]]
    if all(isinstance(item, str) for item in value):
        return [[item.lower() for item in value]]
    groups = []
    for entry in value:
        if isinstance(entry, str):
            groups.append([entry.lower()])
        else:
            groups.append([str(item).lower() for item in entry])
    return [g for g in groups if g]


@dataclass
class MatchConfig:
    """Filters applied to every posting a source returns."""

    title_include: list[list[str]] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)
    location_include: list[str] = field(default_factory=list)
    location_exclude: list[str] = field(default_factory=list)
    company_exclude: list[str] = field(default_factory=list)
    description_exclude: list[str] = field(default_factory=list)
    remote_only: bool = False
    #: Whether a remote role satisfies location_include regardless of the city
    #: named. True suits "anywhere is fine"; set False when the search is tied
    #: to one country, where a remote role elsewhere is not actually workable.
    remote_satisfies_location: bool = True
    max_age_days: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MatchConfig":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(
                f"unknown key(s) under 'match': {', '.join(sorted(unknown))}. "
                f"Valid keys: {', '.join(sorted(known))}"
            )
        return cls(
            title_include=_keyword_groups(data.get("title_include")),
            title_exclude=[s.lower() for s in data.get("title_exclude", [])],
            location_include=[s.lower() for s in data.get("location_include", [])],
            location_exclude=[s.lower() for s in data.get("location_exclude", [])],
            company_exclude=[s.lower() for s in data.get("company_exclude", [])],
            description_exclude=[s.lower() for s in data.get("description_exclude", [])],
            remote_only=bool(data.get("remote_only", False)),
            remote_satisfies_location=bool(data.get("remote_satisfies_location", True)),
            max_age_days=data.get("max_age_days"),
        )

    def __post_init__(self) -> None:
        # Accept a flat list from direct construction, not just from YAML.
        self.title_include = _keyword_groups(self.title_include)


@dataclass
class SourceConfig:
    """One configured source: its plugin type plus that plugin's options."""

    type: str
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.type


@dataclass
class NotifierConfig:
    type: str
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    sources: list[SourceConfig]
    notifiers: list[NotifierConfig]
    match: MatchConfig = field(default_factory=MatchConfig)
    state_path: Path = Path("state/seen.json")
    max_per_run: int = 50
    notify_on_first_run: bool = False
    request_timeout: float = 20.0
    user_agent: str = (
        "jobsearch/0.1 (+https://github.com/rbrhou/job-search) personal job alert bot"
    )

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists():
            raise ConfigError(
                f"no config at {path}. Copy config.example.yaml to {path} and edit it."
            )
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level")
        return cls.from_dict(_expand_env(raw))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        sources = []
        for entry in raw.get("sources", []):
            if not isinstance(entry, dict) or "type" not in entry:
                raise ConfigError("each item under 'sources' needs a 'type' key")
            opts = {k: v for k, v in entry.items() if k not in ("type", "enabled")}
            sources.append(
                SourceConfig(
                    type=entry["type"],
                    enabled=entry.get("enabled", True),
                    options=opts,
                )
            )
        if not sources:
            raise ConfigError("config has no 'sources' — nothing to search")

        notifiers = []
        for entry in raw.get("notifiers", []):
            if not isinstance(entry, dict) or "type" not in entry:
                raise ConfigError("each item under 'notifiers' needs a 'type' key")
            opts = {k: v for k, v in entry.items() if k not in ("type", "enabled")}
            notifiers.append(
                NotifierConfig(
                    type=entry["type"],
                    enabled=entry.get("enabled", True),
                    options=opts,
                )
            )

        return cls(
            sources=sources,
            notifiers=notifiers,
            match=MatchConfig.from_dict(raw.get("match", {}) or {}),
            state_path=Path(raw.get("state_path", "state/seen.json")),
            max_per_run=int(raw.get("max_per_run", 50)),
            notify_on_first_run=bool(raw.get("notify_on_first_run", False)),
            request_timeout=float(raw.get("request_timeout", 20.0)),
            user_agent=raw.get("user_agent", cls.user_agent),
        )

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    @property
    def enabled_notifiers(self) -> list[NotifierConfig]:
        return [n for n in self.notifiers if n.enabled]
