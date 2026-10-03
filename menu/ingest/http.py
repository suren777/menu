"""Shared HTTP session for the ingest.

One session for every request: connections are pooled across the crawl,
requests carry a real User-Agent, and transient failures (429/5xx) are
retried with backoff instead of being counted as crawl failures.
"""

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

USER_AGENT = "menu/0.1 (recipe sourcing lab; +https://github.com/surenislyaev/menu)"

_RETRIES = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
)

session = Session()
session.headers["User-Agent"] = USER_AGENT
_adapter = HTTPAdapter(max_retries=_RETRIES)
session.mount("https://", _adapter)
session.mount("http://", _adapter)
