import pytest

from app.db.session import make_engine


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_postgres_urls_work_with_either_scheme(scheme: str) -> None:
    engine = make_engine(f"{scheme}://user:pw@db.example.com:5432/proofcheck")
    assert engine.url.drivername.startswith("postgresql")
    assert (engine.url.host, engine.url.port, engine.url.database) == (
        "db.example.com",
        5432,
        "proofcheck",
    )
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]


def test_sqlite_keeps_its_thread_setting_and_skips_the_ping(tmp_path: object) -> None:
    engine = make_engine(f"sqlite:///{tmp_path}/x.db")
    assert engine.url.drivername == "sqlite"
    assert engine.pool._pre_ping is False  # type: ignore[attr-defined]
    with engine.connect() as connection:  # check_same_thread=False: usable across threads
        assert connection.exec_driver_sql("select 1").scalar() == 1
