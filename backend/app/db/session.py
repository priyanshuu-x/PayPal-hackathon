"""Engine and session handling (SQLite locally, Postgres on Render)."""

from collections.abc import Iterator

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.db import models  # noqa: F401  (registers tables)


def make_engine(database_url: str) -> Engine:
    # Some providers still hand out postgres:// URLs, which SQLAlchemy 2 refuses.
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url.removeprefix("postgres://")
    sqlite = database_url.startswith("sqlite")
    return create_engine(
        database_url,
        connect_args={"check_same_thread": False} if sqlite else {},
        pool_pre_ping=not sqlite,  # a managed Postgres closes idle connections; test before use
    )


def create_db_and_tables(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
