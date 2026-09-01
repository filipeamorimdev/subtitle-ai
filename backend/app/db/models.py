"""SQLAlchemy models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


class SettingsRow(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    bazarr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bazarr_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    jellyfin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jellyfin_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    openrouter_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    openrouter_model: Mapped[str] = mapped_column(String(256), default="openai/gpt-4o-mini")
    target_language_code: Mapped[str] = mapped_column(String(32), default="pt-PT")
    target_language_name: Mapped[str] = mapped_column(String(128), default="Portuguese (Portugal)")
    source_languages: Mapped[list[Any]] = mapped_column(JSON, default=lambda: ["en"])
    media_roots: Mapped[list[Any]] = mapped_column(JSON, default=lambda: ["/media"])
    path_mappings: Mapped[list[Any]] = mapped_column(JSON, default=lambda: [])
    batch_size: Mapped[int] = mapped_column(Integer, default=25)
    max_concurrent_translate: Mapped[int] = mapped_column(Integer, default=1)
    max_concurrent_extract: Mapped[int] = mapped_column(Integer, default=1)
    max_concurrent_request: Mapped[int] = mapped_column(Integer, default=1)
    max_concurrent_transcribe: Mapped[int] = mapped_column(Integer, default=1)
    max_concurrent_dub: Mapped[int] = mapped_column(Integer, default=1)
    asr_local_model: Mapped[str] = mapped_column(String(32), default="small")
    automatic_fallback_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    automatic_scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=5)
    bazarr_grace_period_minutes: Mapped[int] = mapped_column(Integer, default=10)
    automatic_retry_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    maximum_automatic_retries: Mapped[int] = mapped_column(Integer, default=3)
    openrouter_log_full_exchanges: Mapped[bool] = mapped_column(Boolean, default=False)
    openrouter_temperature: Mapped[float] = mapped_column(Float, default=0.0)
    routing_strategy: Mapped[str] = mapped_column(String(32), default="free_first")
    allow_paid_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_free_fallback: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_unknown_pricing: Mapped[bool] = mapped_column(Boolean, default=False)
    maximum_cost_per_job_micro_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_budget_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    monthly_budget_amount_micro_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allow_manual_budget_override: Mapped[bool] = mapped_column(Boolean, default=False)
    require_translation_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    operator_model_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpenRouterModelPreferenceRow(Base):
    """User-configured free/paid model pools with explicit priority."""

    __tablename__ = "openrouter_model_preferences"
    __table_args__ = (
        UniqueConstraint("model_id", name="uq_openrouter_model_preferences_model_id"),
        UniqueConstraint("tier", "model_id", name="uq_openrouter_model_preferences_tier_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # free | paid
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OpenRouterCatalogCacheRow(Base):
    """Cached OpenRouter model catalog metadata (volatile; not cost authority).

    Legacy table — prefer AiModelCatalogCacheRow. Kept for alpha1 compatibility.
    """

    __tablename__ = "openrouter_catalog_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload_json: Mapped[list[Any] | dict[str, Any]] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stale: Mapped[bool] = mapped_column(Boolean, default=False)


class AiProviderAccountRow(Base):
    """BYOAI provider credentials — one account may own many models."""

    __tablename__ = "ai_provider_accounts"
    __table_args__ = (UniqueConstraint("provider_id", name="uq_ai_provider_accounts_provider_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiModelPreferenceRow(Base):
    """Provider-neutral model pools, scoped to their intended workload."""

    __tablename__ = "ai_model_preferences"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "model_id",
            "purpose",
            name="uq_ai_model_preferences_provider_model_purpose",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # free | paid
    # ``audio_analysis`` is deliberately separate from translation routing so a
    # multimodal model can be configured for both workloads independently.
    purpose: Mapped[str] = mapped_column(
        String(32), nullable=False, default="translation", index=True
    )  # translation | audio_analysis
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiModelCatalogCacheRow(Base):
    """Provider-aware model catalog cache (one row per provider)."""

    __tablename__ = "ai_model_catalog_cache"

    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[list[Any] | dict[str, Any]] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stale: Mapped[bool] = mapped_column(Boolean, default=False)


class AiUsageRecordRow(Base):
    """Authoritative per-request AI usage and historical pricing snapshot."""

    __tablename__ = "ai_usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    operation_type: Mapped[str] = mapped_column(String(64), index=True)  # translation, repair, model_test, ...
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    provider_id: Mapped[str] = mapped_column(String(64), default="openrouter", index=True)
    model_id: Mapped[str] = mapped_column(String(256), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[str] = mapped_column(String(16), default="unknown", index=True)  # free | paid | unknown
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_micro_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_cost_micro_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_price_micro_usd_per_million: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_price_micro_usd_per_million: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pricing_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pricing_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AiBudgetReservationRow(Base):
    """Lightweight SQLite budget reservation to prevent concurrent overspend."""

    __tablename__ = "ai_budget_reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    month_key: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM UTC
    amount_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AiRoutingEventRow(Base):
    """Recent routing decisions for the AI dashboard (no secrets/content)."""

    __tablename__ = "ai_routing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)  # selected | fallback | blocked
    strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    next_provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_model_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)


class MediaItemRow(Base):
    """Lightweight media identity cache (not a full media-management library)."""

    __tablename__ = "media_items"
    __table_args__ = (
        UniqueConstraint("provider_id", "external_id", name="uq_media_items_provider_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # movie|series|episode
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    bazarr_movie_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    bazarr_series_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    bazarr_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_media_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("media_items.id"), nullable=True, index=True
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VoiceCastDraftRow(Base):
    """An editable, persistent AI voice-casting proposal for one dub language."""

    __tablename__ = "voice_cast_drafts"
    __table_args__ = (
        UniqueConstraint(
            "media_item_id",
            "target_language",
            name="uq_voice_cast_drafts_media_language",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_items.id"), nullable=False, index=True
    )
    target_language: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    analysed_cue_count: Mapped[int] = mapped_column(Integer, default=0)
    mix_mode: Mapped[str] = mapped_column(String(32), default="background_preserved")
    suggestions_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    media_item = relationship("MediaItemRow", lazy="joined")


class LocalizationTaskRow(Base):
    """User-facing localization goal for a media item (subtitles today; audio later)."""

    __tablename__ = "localization_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_items.id"), nullable=False, index=True
    )
    target_language_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_language_name: Mapped[str] = mapped_column(String(128), nullable=False)
    capability: Mapped[str] = mapped_column(String(32), default="subtitles", index=True)
    status: Mapped[str] = mapped_column(String(32), default="requested", index=True)
    substate: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="manual", index=True)  # manual|automatic
    priority: Mapped[str] = mapped_column(String(16), default="high", index=True)  # high|normal
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    media_item = relationship("MediaItemRow", lazy="joined")


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_key: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("localization_tasks.id"), nullable=True, index=True
    )
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
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    progress_detail: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extract_stream_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dub_mix_mode: Mapped[str] = mapped_column(
        String(32), default="background_preserved", server_default="background_preserved"
    )
    dub_speaker_voices: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ObservedCandidateRow(Base):
    """Persistent observation of Bazarr wanted items for grace period / automation."""

    __tablename__ = "observed_candidates"

    candidate_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(32), default="movie")
    media_path: Mapped[str] = mapped_column(String(1024))
    media_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_language: Mapped[str] = mapped_column(String(32), default="pt-PT")
    bazarr_movie_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bazarr_episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bazarr_series_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_automatic_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    automatic_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currently_wanted: Mapped[bool] = mapped_column(Boolean, default=True)


class TranslationCacheRow(Base):
    __tablename__ = "translation_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    target_language: Mapped[str] = mapped_column(String(32))
    provider_id: Mapped[str] = mapped_column(String(64), default="openrouter", index=True)
    model: Mapped[str] = mapped_column(String(256))
    target_subtitle_path: Mapped[str] = mapped_column(String(1024))
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GlossaryEntryRow(Base):
    """Locked name map for a series or movie + target language."""

    __tablename__ = "glossary_entries"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "target_language",
            "source_normalized",
            name="uq_glossary_entries_scope_lang_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_language: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(512), nullable=False)
    source_normalized: Mapped[str] = mapped_column(String(512), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LocalePromptNoteRow(Base):
    """Optional override for locale translator notes."""

    __tablename__ = "locale_prompt_notes"

    language_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OperatorChatSessionRow(Base):
    """In-app operator chat session (dashboard ask bar)."""

    __tablename__ = "operator_chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OperatorChatMessageRow(Base):
    """Persisted operator chat turns (tool-call JSON allowed; never subtitle bodies)."""

    __tablename__ = "operator_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("operator_chat_sessions.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user|assistant|tool|system
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
