"""Shared ingest exceptions."""


class FetchError(Exception):
    """Raised when a site cannot be fetched."""


class DisallowedError(FetchError):
    """robots.txt disallows fetching this URL."""
