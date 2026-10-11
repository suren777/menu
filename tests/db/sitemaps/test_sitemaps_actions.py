from typing import TYPE_CHECKING

from menu.db.database import Sitemap
from menu.db.sitemaps import actions, repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_set_status(session: Session) -> None:
    """set_status opens its own session on the (patched) default
    engine, so the row must be committed for it to be visible."""
    session.add(Sitemap(url="http://test.com", site="test", status="pending"))
    session.commit()

    actions.set_status("http://test.com", "test", "failed", error="ParseError: junk")

    model = repository.find_by_url("http://test.com", session)
    assert model is not None
    assert model.status == "failed"
    assert model.error == "ParseError: junk"
