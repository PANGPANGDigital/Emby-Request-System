from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    requests: Mapped[list[MediaRequest]] = relationship(back_populates="applicant")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Media(Base):
    __tablename__ = "media"
    __table_args__ = (UniqueConstraint("tmdb_id", "media_type", name="uq_media_tmdb_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, index=True)
    media_type: Mapped[str] = mapped_column(String(10))  # 媒体类型：movie（电影）或 tv（剧集）
    title: Mapped[str] = mapped_column(String(300))
    original_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    release_year: Mapped[str | None] = mapped_column(String(4), nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    emby_item_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_in_emby: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    emby_seasons: Mapped[str | None] = mapped_column(Text, nullable=True)  # 已入库季数的 JSON 数组，例如 "[1,2,3]"
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    requests: Mapped[list[MediaRequest]] = relationship(back_populates="media")


class MediaRequest(Base):
    __tablename__ = "media_requests"
    __table_args__ = (
        UniqueConstraint("media_id", "applicant_id", "request_type", "season_number", name="uq_request_by_user_media_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(12))  # 申请类型：request（求片）或 follow（追新）
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 剧集追新时指定的季数
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    media: Mapped[Media] = relationship(back_populates="requests")
    applicant: Mapped[User] = relationship(back_populates="requests")
