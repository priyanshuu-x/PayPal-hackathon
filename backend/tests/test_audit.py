from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.models import AuditEvent
from app.db.session import create_db_and_tables, make_engine
from app.security.audit import GENESIS_HASH, append_event, verify_chain


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = make_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    create_db_and_tables(engine)
    with Session(engine) as session:
        yield session


def test_empty_chain_is_valid(db: Session) -> None:
    assert verify_chain(db)


def test_events_are_linked_from_genesis(db: Session) -> None:
    first = append_event(db, case_id="c1", actor="system", action="a")
    second = append_event(db, case_id="c1", actor="merchant:demo", action="b", details={"k": 1})
    db.commit()
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert first.hash != second.hash
    assert verify_chain(db)


def test_editing_an_event_breaks_the_chain(db: Session) -> None:
    append_event(db, case_id="c1", actor="system", action="a")
    append_event(db, case_id="c1", actor="system", action="b")
    db.commit()
    victim = db.exec(select(AuditEvent).where(AuditEvent.action == "a")).one()
    victim.action = "approved"
    db.add(victim)
    db.commit()
    assert not verify_chain(db)


def test_deleting_a_middle_event_breaks_the_chain(db: Session) -> None:
    for action in ("a", "b", "c"):
        append_event(db, case_id=None, actor="system", action=action)
    db.commit()
    middle = db.exec(select(AuditEvent).where(AuditEvent.action == "b")).one()
    db.delete(middle)
    db.commit()
    assert not verify_chain(db)


def test_details_are_redacted(db: Session) -> None:
    event = append_event(
        db, case_id=None, actor="system", action="a", details={"api_key": "abc", "n": 2}
    )
    assert "abc" not in event.details_json
    assert '"n":2' in event.details_json


def test_two_writers_cannot_extend_the_chain_from_the_same_event(db: Session) -> None:
    last = append_event(db, case_id=None, actor="system", action="a")
    db.commit()
    db.add(
        AuditEvent(
            case_id=None,
            actor="x",
            action="fork",
            created_at="2026-01-01T00:00:00.000000Z",
            prev_hash=GENESIS_HASH,  # same predecessor as `last`
            hash="f" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert last.prev_hash == GENESIS_HASH
