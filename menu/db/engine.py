from pathlib import Path

from sqlalchemy import create_engine

DB_PATH = Path(__file__).resolve().parent / "database.db"
engine = create_engine(f"sqlite:///{DB_PATH}")
