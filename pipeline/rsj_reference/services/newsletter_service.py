"""Proxy export for newsletter_service."""
from redsun.services.newsletter_service import (
    NewsletterService,
    compose_weekly_dispatch,
)

__all__ = [
    "NewsletterService",
    "compose_weekly_dispatch",
]
