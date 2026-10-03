"""Shared ingest exceptions."""


class FetchError(Exception):
    """Raised when a site cannot be fetched."""


class DisallowedError(FetchError):
    """robots.txt disallows fetching this URL."""


class BlockedError(FetchError):
    """The site is blocking us: 401/402/403, or a 429 that never clears."""


class CrawlBlockedError(Exception):
    """A site-wide block: the crawl stops, unfinished sitemaps stay queued."""
