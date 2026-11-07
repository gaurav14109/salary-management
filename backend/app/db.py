from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


# create_engine does not connect; the pool opens a connection on first use.
# That keeps importing the app cheap and lets the test suite start without a database.
engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def streaming_session() -> Iterator[Session]:
    """A session owned by a response body rather than by a request.

    A StreamingResponse generates its body *after* dependencies have torn down, so a
    generator holding the request's session reads from a closed one - and does it
    only on the large exports, in production, where the first rows arrive and the
    rest do not. A streaming route opens its own and closes it when the generator
    finishes.

    No commit: streaming routes read.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_streaming_session():
    """Injected rather than imported, so a test can supply its own.

    Without this the export would be the one route whose data a transactional test
    cannot see - it would open a second connection and find an empty database.
    """
    return streaming_session


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
