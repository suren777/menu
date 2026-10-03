from menu.db.connection import get_session
from menu.db.database import Sitemap


def finalise_sitemap(url: str) -> None:
    with get_session() as session:
        session.query(Sitemap).filter(Sitemap.url == url).update({"completed": True})
