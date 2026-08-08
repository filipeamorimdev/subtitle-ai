"""SQLAlchemy models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base


class SettingsRow(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bazarr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bazarr_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    openrouter_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    openrouter_model: Mapped[str] = mapped_column(String(256), default="openai/gpt-4o-mini")
    target_language_code: Mapped[str] = mapped_column(String(32), default="pt-PT")
    target_language_name: Mapped[str] = mapped_column(String(128), default="Portuguese (Portugal)")
    source_languages: Mapped[list[Any]] = mapped_column(JSON, default=lambda: ["en"])
    media_roots: Mapped[list[Any]] = mapped_column(JSON, default=lambda: ["/media"])
    path_mappings: Mapped[list[Any]] = mapped_column(JSON, default=lambda: [])
    batch_size: Mapped[int] = mapped_column(Integer, default=50)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_key: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    job_kind: Mapped[str] = mapped_column(String(32), default="translate", index=True)
    media_type: Mapped[str] = mapped_column(String(32), default="movie")
    media_path: Mapped[str] = mapped_column(String(1024))
    media_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bazarr_movie_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bazarr_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bazarr_series_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_subtitle_path: Mapped[str] = mapped_column(String(1024))
    target_subtitle_path: Mapped[str] = mapped_column(String(1024))
    source_language: Mapped[str] = mapped_column(String(32), default="en")
    target_language: Mapped[str] = mapped_column(String(32), default="pt-PT")
    model: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    progress_detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extract_stream_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TranslationCacheRow(Base):
    __tablename__ = "translation_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    target_language: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(256))
    target_subtitle_path: Mapped[str] = mapped_column(String(1024))
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
