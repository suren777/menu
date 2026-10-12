from typing import TYPE_CHECKING

from menu.db.database import Sitemap
from menu.db.sitemaps import actions, repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_set_status(session: Session) -> None:
    session.add(Sitemap(url="http://test.com", site="test", status="pending"))
    session.flush()

    actions.set_status(
        "http://test.com", "test", "failed", session, error="ParseError: junk"
    )

    model = repository.find_by_url("http://test.com", session)
    assert model is not None
    assert model.status == "failed"
    assert model.error == "ParseError: junk"
