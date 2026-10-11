"""Shared ingest exceptions."""


class FetchError(Exception):
    """Raised when a site cannot be fetched.

    status_code carries the HTTP status when the failure came from a
    response, so the crawl ledger can record it.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DisallowedError(FetchError):
    """robots.txt disallows fetching this URL."""


class BlockedError(FetchError):
    """The site is blocking us: 401/402/403, or a 429 that never clears."""


class CrawlBlockedError(Exception):
    """A site-wide block: the crawl stops, unfinished work stays queued."""
