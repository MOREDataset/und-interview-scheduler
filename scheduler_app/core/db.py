"""Database engine and session helpers."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base, SchemaInfo

SCHEMA_VERSION = 1


def make_engine(url: str) -> Engine:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        if ":///" in url and ":memory:" not in url:
            Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            # WAL lets readers and one writer work at the same time; safer for multi-user.
            if ":memory:" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    return make_engine(url or get_settings().database_url)


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False, future=True)


def init_db(engine: Engine | None = None) -> None:
    """Create all tables if missing and record the schema version. Safe to run repeatedly."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        if s.scalar(select(SchemaInfo).limit(1)) is None:
            s.add(SchemaInfo(version=SCHEMA_VERSION))
            s.commit()


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error."""
    s = get_sessionmaker(engine)()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
