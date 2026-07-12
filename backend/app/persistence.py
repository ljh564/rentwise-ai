import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AnonymousUser(Base):
    __tablename__ = "anonymous_users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RentalProfile(Base):
    __tablename__ = "rental_profiles"

    anonymous_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("anonymous_users.id", ondelete="CASCADE"), primary_key=True)
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("anonymous_user_id", "listing_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    anonymous_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("anonymous_users.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[str] = mapped_column(String(120), nullable=False)
    listing_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    anonymous_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("anonymous_users.id", ondelete="CASCADE"), index=True)
    request_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RecommendationFeedback(Base):
    __tablename__ = "recommendation_feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    anonymous_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("anonymous_users.id", ondelete="CASCADE"), index=True)
    search_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("search_history.id", ondelete="SET NULL"), nullable=True)
    listing_id: Mapped[str] = mapped_column(String(120), nullable=False)
    feedback_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    anonymous_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("anonymous_users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    trace: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://rentscout:rentscout_dev@postgres:5432/rentscout")
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_schema() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def create_anonymous_session(db: AsyncSession) -> tuple[AnonymousUser, str]:
    token = secrets.token_urlsafe(32)
    user = AnonymousUser(access_token_hash=hash_token(token))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user, token


async def authenticate_anonymous(db: AsyncSession, user_id: str, token: str) -> AnonymousUser | None:
    try:
        parsed_id = uuid.UUID(user_id)
    except ValueError:
        return None
    user = await db.scalar(select(AnonymousUser).where(AnonymousUser.id == parsed_id))
    if not user or not secrets.compare_digest(user.access_token_hash, hash_token(token)):
        return None
    user.last_seen_at = datetime.now(timezone.utc)
    return user
