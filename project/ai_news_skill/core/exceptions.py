"""Custom exceptions for the AI news digest pipeline."""

from __future__ import annotations


class DigestError(Exception):
    """Base exception for all digest pipeline errors."""


class ConfigError(DigestError):
    """Invalid or missing configuration."""


class CollectionError(DigestError):
    """Error during news collection (RSS, crawlers, APIs)."""


class EnrichmentError(DigestError):
    """Error during LLM enrichment stage."""


class LLMClientError(DigestError):
    """Error communicating with a language model API."""


class WebhookError(DigestError):
    """Error sending webhook notification."""
