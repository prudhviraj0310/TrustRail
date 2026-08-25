"""Database engine, session factory and declarative base.

PostgreSQL is the intended source of truth for transaction state; SQLite is the
zero-setup local/test backend. SQLAlchemy 2.0 makes the two interchangeable.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def make_engine(url: str) -> Engine:
    """Build an engine, applying SQLite-specific pragmas where needed."""
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # An in-memory SQLite DB only survives if every connection shares the
        # same underlying connection (StaticPool).
        if url in ("sqlite://", "sqlite:///:memory:"):
            return create_engine(
                url, connect_args=connect_args, poolclass=StaticPool, future=True
            )
        return create_engine(url, connect_args=connect_args, future=True)
    # PostgreSQL (or any server DB): validate connections before use.
    return create_engine(url, pool_pre_ping=True, future=True)


engine: Engine = make_engine(get_settings().database_url)

# expire_on_commit=False keeps ORM objects usable after a commit inside a
# service call (we commit per state transition, then keep reading the object).
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    """Create all tables (dev/test convenience; prod uses Alembic)."""
    # Importing the models package registers every table on Base.metadata.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
