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


class OpenRouterModelOut(BaseModel):
    id: str
    name: str
    prompt_price_per_million: float
    completion_price_per_million: float
    context_length: int | None = None


class OpenRouterModelsOut(BaseModel):
    models: list[OpenRouterModelOut]


class EmbeddedSubtitleOut(BaseModel):
    language: str | None = None
    codec: str | None = None
    kind: Literal["text", "image", "unknown"] = "unknown"
    extractable: bool = False
    stream_index: int | None = None
    hi: bool = False
    forced: bool = False
    title: str | None = None
    source: str = "bazarr"
    label: str


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
    embedded_subtitles: list[EmbeddedSubtitleOut] = Field(default_factory=list)
    has_embedded: bool = False
    can_extract: bool = False
    extract_stream_index: int | None = None
    extract_language: str | None = None
    active_extract_job_id: int | None = None
    active_request_job_id: int | None = None
    latest_job_id: int | None = None


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


class ExtractCreate(BaseModel):
    candidate_key: str


class RequestSubtitleCreate(BaseModel):
    candidate_key: str
    language: str | None = None


class RequestSubtitleResult(BaseModel):
    ok: bool
    message: str
    language: str
    media_type: Literal["movie", "episode"]
    title: str
    bazarr_movie_id: int | None = None
    bazarr_episode_id: int | None = None
    bazarr_series_id: int | None = None


class JobOut(BaseModel):
    id: int
    candidate_key: str | None
    job_kind: str = "translate"
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
    extract_stream_index: int | None = None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


class BatchJobsOut(BaseModel):
    jobs: list[JobOut] = Field(default_factory=list)
    created_count: int = 0
    reused_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)


class JobLogOut(BaseModel):
    job_id: int
    exists: bool
    path: str
    entry_count: int = 0
    content: str | None = None
    entries: list[dict[str, Any]] | None = None


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


class GlossaryScopeOut(BaseModel):
    id: int
    kind: str
    key: str
    display_name: str
    target_language: str
    parent_scope_id: int | None
    bazarr_series_id: int | None
    bazarr_movie_id: int | None
    term_count: int = 0
    suggested_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GlossaryScopeCreate(BaseModel):
    kind: Literal["universe", "series", "movie"]
    key: str
    display_name: str
    target_language: str
    parent_scope_id: int | None = None
    bazarr_series_id: int | None = None
    bazarr_movie_id: int | None = None


class GlossaryScopeUpdate(BaseModel):
    display_name: str | None = None
    parent_scope_id: int | None = None
    clear_parent: bool = False


class GlossaryTermOut(BaseModel):
    id: int
    scope_id: int
    source: str
    target: str
    term_type: str
    policy: str
    status: str
    locked: bool
    source_origin: str
    notes: str | None = None
    scope_kind: str | None = None
    scope_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GlossaryTermCreate(BaseModel):
    source: str
    target: str
    term_type: str = "other"
    policy: str = "keep"
    status: str = "active"
    locked: bool = False
    notes: str | None = None


class GlossaryTermUpdate(BaseModel):
    source: str | None = None
    target: str | None = None
    term_type: str | None = None
    policy: str | None = None
    status: str | None = None
    locked: bool | None = None
    notes: str | None = None


class GlossaryTermReview(BaseModel):
    approve: bool
    lock: bool = False


class GlossaryUniverseOut(BaseModel):
    key: str
    display_name: str
