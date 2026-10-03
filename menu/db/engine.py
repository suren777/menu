"""Scratch SQLite engine.

The database file lives at the project root (gitignored), not inside
the package: it is throwaway experiment storage, rebuilt from scratch
whenever the schema changes.
"""

from pathlib import Path

from sqlalchemy import create_engine

DB_PATH = Path(__file__).resolve().parents[2] / "database.db"

engine = create_engine(f"sqlite:///{DB_PATH}")
