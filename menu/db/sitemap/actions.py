from sqlalchemy import update

from menu.db.connection import get_session
from menu.db.database import Sitemap


def finalise_sitemap(url: str, site: str) -> None:
    with get_session() as session:
        session.execute(
            update(Sitemap)
            .where(Sitemap.url == url, Sitemap.site == site)
            .values(completed=True)
        )
