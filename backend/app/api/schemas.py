"""Pydantic API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LanguageOut(BaseModel):
    code: str
    name: str


class PathMappingIn(BaseModel):
    bazarr_prefix: str
    local_prefix: str


class SettingsUpdate(BaseModel):
    bazarr_url: str | None = None
    bazarr_api_key: str | None = None
    clear_bazarr_api_key: bool = False
    openrouter_api_key: str | None = None
    clear_openrouter_api_key: bool = False
    openrouter_model: str | None = None
    target_language_code: str | None = None
    target_language_name: str | None = None
    source_languages: list[str] | None = None
    media_roots: list[str] | None = None
    path_mappings: list[PathMappingIn] | None = None
    batch_size: int | None = Field(default=None, ge=1, le=200)


class SettingsOut(BaseModel):
    bazarr_url: str | None
    bazarr_api_key_masked: str | None
    bazarr_api_key_configured: bool
    openrouter_api_key_masked: str | None
    openrouter_api_key_configured: bool
    openrouter_model: str
    target_language: LanguageOut
    source_languages: list[str]
    media_roots: list[str]
    path_mappings: list[PathMappingIn]
    batch_size: int


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] | None = None


class CandidateOut(BaseModel):
    key: str
    media_type: Literal["movie", "episode"]
    title: str
    media_path: str
    bazarr_movie_id: int | None = None
    bazarr_episode_id: int | None = None
    bazarr_series_id: int | None = None
    target_language: str
    source_language: str | None = None
    source_subtitle_path: str | None = None
    target_subtitle_path: str | None = None
    can_translate: bool
    reason_code: str | None = None
    reason: str | None = None


class JobCreate(BaseModel):
    candidate_key: str | None = None
    source_subtitle_path: str | None = None
    target_language: str | None = None
    media_type: Literal["movie", "episode"] | None = "movie"
    media_path: str | None = None
    media_title: str | None = None
    bazarr_movie_id: int | None = None
    bazarr_episode_id: int | None = None
    bazarr_series_id: int | None = None
    source_language: str | None = None


class JobOut(BaseModel):
    id: int
    candidate_key: str | None
    media_type: str
    media_path: str
    media_title: str | None
    bazarr_movie_id: int | None
    bazarr_episode_id: int | None
    bazarr_series_id: int | None
    source_subtitle_path: str
    target_subtitle_path: str
    source_language: str
    target_language: str
    model: str
    status: str
    progress: float
    progress_detail: str | None
    error: str | None
    warning: str | None
    reason_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


class StatsOut(BaseModel):
    pending: int
    processing: int
    completed: int
    failed: int
    cancelled: int
    skipped: int
    total: int


class HealthOut(BaseModel):
    status: str
    version: str
    database: str = "healthy"
    bazarr: str = "unknown"
    openrouter: str = "unknown"
