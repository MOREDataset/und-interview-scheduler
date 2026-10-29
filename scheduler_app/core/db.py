"""Database engine and session helpers."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.schema import CreateColumn
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base, SchemaInfo

SCHEMA_VERSION = 2


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


def _add_missing_columns(engine: Engine) -> list[str]:
    """Bring an existing database up to date by adding columns introduced in newer
    versions of the app. Only ever ADDS things; existing data is never touched."""
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = str(CreateColumn(col).compile(dialect=engine.dialect))
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {ddl}'))
                added.append(f"{table.name}.{col.name}")
        if engine.dialect.name == "postgresql":
            # v2: event instructions can be longer than 500 characters.
            conn.execute(text("ALTER TABLE fixed_events ALTER COLUMN public_note TYPE TEXT"))
    return added


def init_db(engine: Engine | None = None) -> None:
    """Create missing tables, add missing columns, record the schema version.
    Safe to run repeatedly, including against a database created by an older version."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    with Session(engine) as s:
        info = s.scalar(select(SchemaInfo).order_by(SchemaInfo.id.desc()).limit(1))
        if info is None or info.version < SCHEMA_VERSION:
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
