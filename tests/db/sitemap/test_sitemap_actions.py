from typing import TYPE_CHECKING

from menu.db.database import Sitemap
from menu.db.sitemap import actions, repository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_finalise_sitemap(session: Session) -> None:
    """finalise_sitemap opens its own session on the (patched) default
    engine, so the row must be committed for it to be visible."""
    session.add(Sitemap(url="http://test.com", site="test", completed=False))
    session.commit()

    actions.finalise_sitemap("http://test.com", "test")

    model = repository.find_by_url("http://test.com", session)
    assert model is not None
    assert model.completed
