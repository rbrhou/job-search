"""Notification channels. Importing this package registers every built-in plugin."""

from .base import Notifier, available_notifiers, get_notifier, register  # noqa: F401
from . import console, discord  # noqa: F401,E402

__all__ = ["Notifier", "available_notifiers", "get_notifier", "register"]
