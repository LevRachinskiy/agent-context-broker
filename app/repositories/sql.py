import hashlib
import json
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.errors import Conflict, NotFound
from app.models.schemas import Event, EventCreate, EventKind, Session, SessionCreate, new_id, now


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    token_budget: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("session_id", "idempotency_key"),)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(String(16000))
    importance: Mapped[float] = mapped_column(Float)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    embedding: Mapped[list[float]] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))


def session_schema(row: SessionRow) -> Session:
    return Session(
        session_id=row.session_id,
        agent_id=row.agent_id,
        metadata=row.metadata_json,
        token_budget=row.token_budget,
        created_at=datetime.fromisoformat(row.created_at),
        updated_at=datetime.fromisoformat(row.updated_at),
        revision=row.revision,
    )


def event_schema(row: EventRow) -> Event:
    return Event(
        event_id=row.event_id,
        session_id=row.session_id,
        kind=EventKind(row.kind),
        content=row.content,
        importance=row.importance,
        metadata=row.metadata_json,
        embedding=row.embedding,
        created_at=datetime.fromisoformat(row.created_at),
    )


class Repository(Protocol):
    async def create_session(self, data: SessionCreate) -> Session: ...
    async def get_session(self, session_id: str) -> Session: ...
    async def append(
        self, session_id: str, data: EventCreate, embedding: list[float], key: str | None = None
    ) -> Event: ...
    async def list_events(
        self, session_id: str, limit: int = 2000, kind: EventKind | None = None, offset: int = 0
    ) -> list[Event]: ...


class SQLRepository:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def create_session(self, data: SessionCreate) -> Session:
        stamp = now().isoformat()
        row = SessionRow(
            session_id=new_id(),
            agent_id=data.agent_id,
            metadata_json=data.metadata,
            token_budget=data.token_budget,
            created_at=stamp,
            updated_at=stamp,
            revision=0,
        )
        async with self.sessions.begin() as db:
            db.add(row)
        return session_schema(row)

    async def get_session(self, session_id: str) -> Session:
        async with self.sessions() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                raise NotFound("Session not found")
            return session_schema(row)

    async def append(
        self, session_id: str, data: EventCreate, embedding: list[float], key: str | None = None
    ) -> Event:
        # Stable fingerprint independent of JSON object insertion order.
        fingerprint = hashlib.sha256(
            json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        stamp = now().isoformat()
        row = EventRow(
            event_id=new_id(),
            session_id=session_id,
            kind=data.kind.value,
            content=data.content,
            importance=data.importance,
            metadata_json=data.metadata,
            embedding=embedding,
            created_at=stamp,
            idempotency_key=key,
            fingerprint=fingerprint,
        )
        try:
            async with self.sessions.begin() as db:
                # Atomic revision increment serializes writes per session in PostgreSQL.
                changed = await db.scalar(
                    update(SessionRow)
                    .where(SessionRow.session_id == session_id)
                    .values(revision=SessionRow.revision + 1, updated_at=stamp)
                    .returning(SessionRow.session_id)
                )
                if changed is None:
                    raise NotFound("Session not found")
                db.add(row)
                await db.flush()
            return event_schema(row)
        except IntegrityError:
            if key is None:
                raise
            async with self.sessions() as db:
                existing = await db.scalar(
                    select(EventRow).where(
                        EventRow.session_id == session_id, EventRow.idempotency_key == key
                    )
                )
                if existing is None:
                    raise
                if existing.fingerprint != fingerprint:
                    raise Conflict("Idempotency key reused with a different payload") from None
                return event_schema(existing)

    async def list_events(
        self, session_id: str, limit: int = 2000, kind: EventKind | None = None, offset: int = 0
    ) -> list[Event]:
        await self.get_session(session_id)
        statement = select(EventRow).where(EventRow.session_id == session_id)
        if kind is not None:
            statement = statement.where(EventRow.kind == kind.value)
        statement = statement.order_by(EventRow.created_at.desc(), EventRow.event_id.desc())
        async with self.sessions() as db:
            rows = (await db.scalars(statement.limit(limit).offset(offset))).all()
            return [event_schema(row) for row in rows]
