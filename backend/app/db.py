from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


# create_engine does not connect; the pool opens a connection on first use.
# That keeps importing the app cheap and lets the test suite start without a database.
engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One session per request, committed on success and rolled back on any exception.

    The transaction boundary lives here rather than in the handlers, so a handler
    cannot leave a half-written state: it does not control the commit.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
