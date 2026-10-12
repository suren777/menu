"""Shared HTTP session for the ingest.

One session for every request: connections are pooled across the crawl,
requests carry a real User-Agent, and transient 5xx failures are
retried with backoff instead of being counted as crawl failures. A
429 is handled one level up (fetch.py waits out Retry-After once, so
the site's own pacing wins over blind retries). The session is built
lazily on first use: importing this module builds nothing.
"""

import functools

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

USER_AGENT = "menu/0.1 (recipe sourcing lab; +https://github.com/surenislyaev/menu)"

_RETRIES = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
)


@functools.cache
def http_session() -> Session:
    """The shared session: built once, on first use."""
    session = Session()
    session.headers["User-Agent"] = USER_AGENT
    adapter = HTTPAdapter(max_retries=_RETRIES)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
