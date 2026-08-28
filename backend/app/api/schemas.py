"""Pydantic API schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.timefmt import DateTimeOut


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
    path_mappings: list[PathMappingIn] | None = None
    batch_size: int | None = Field(default=None, ge=1, le=200)
    max_concurrent_translate: int | None = Field(default=None, ge=1, le=20)
    max_concurrent_extract: int | None = Field(default=None, ge=1, le=20)
    max_concurrent_request: int | None = Field(default=None, ge=1, le=20)
    max_concurrent_transcribe: int | None = Field(default=None, ge=1, le=20)
    max_concurrent_dub: int | None = Field(default=None, ge=1, le=20)
    asr_provider: Literal["local", "openai", "local_then_openai"] | None = None
    asr_local_model: Literal["tiny", "base", "small", "medium", "large-v3", "distil-large-v3"] | None = None
    openai_api_key: str | None = None
    clear_openai_api_key: bool = False
    automatic_fallback_enabled: bool | None = None
    automatic_scan_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    bazarr_grace_period_minutes: int | None = Field(default=None, ge=0, le=1440)
    automatic_retry_enabled: bool | None = None
    maximum_automatic_retries: int | None = Field(default=None, ge=0, le=20)
    openrouter_log_full_exchanges: bool | None = None
    openrouter_temperature: float | None = Field(default=None, ge=0, le=2)
    routing_strategy: Literal["free_only", "paid_only", "free_first", "paid_first"] | None = None
    allow_paid_fallback: bool | None = None
    allow_free_fallback: bool | None = None
    allow_unknown_pricing: bool | None = None
    maximum_cost_per_job_usd: float | None = Field(default=None, ge=0)
    clear_maximum_cost_per_job: bool = False
    monthly_budget_enabled: bool | None = None
    monthly_budget_amount_usd: float | None = Field(default=None, ge=0)
    clear_monthly_budget_amount: bool = False
    allow_manual_budget_override: bool | None = None
    require_translation_approval: bool | None = None
    operator_model_id: str | None = None
    clear_operator_model_id: bool = False


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
    max_concurrent_translate: int
    max_concurrent_extract: int
    max_concurrent_request: int
    max_concurrent_transcribe: int = 1
    max_concurrent_dub: int = 1
    asr_provider: str = "local_then_openai"
    asr_local_model: str = "small"
    openai_api_key_masked: str | None = None
    openai_api_key_configured: bool = False
    automatic_fallback_enabled: bool = False
    automatic_scan_interval_minutes: int = 5
    bazarr_grace_period_minutes: int = 10
    automatic_retry_enabled: bool = True
    maximum_automatic_retries: int = 3
    openrouter_log_full_exchanges: bool = False
    openrouter_temperature: float = 0
    routing_strategy: str = "free_first"
    allow_paid_fallback: bool = False
    allow_free_fallback: bool = True
    allow_unknown_pricing: bool = False
    maximum_cost_per_job_usd: float | None = None
    monthly_budget_enabled: bool = False
    monthly_budget_amount_usd: float | None = None
    allow_manual_budget_override: bool = False
    require_translation_approval: bool = False
    operator_model_id: str | None = None


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] | None = None


class ClearDataResult(BaseModel):
    deleted: int = 0
    message: str
    details: dict[str, Any] | None = None


class ClearJobsRequest(BaseModel):
    job_kind: Literal["translate", "extract", "request", "transcribe", "dub"] | None = None
    status: Literal["failed", "skipped", "cancelled"] | None = None


class OpenRouterModelOut(BaseModel):
    id: str
    name: str
    prompt_price_per_million: float | None = None
    completion_price_per_million: float | None = None
    context_length: int | None = None
    pricing_tier: str | None = None
    description: str | None = None
    compatible: bool | None = None
    compatibility_reason: str | None = None
    stale: bool | None = None
    unavailable: bool | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None


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
    can_transcribe: bool = False
    extract_stream_index: int | None = None
    extract_language: str | None = None
    active_extract_job_id: int | None = None
    active_request_job_id: int | None = None
    active_translate_job_id: int | None = None
    active_transcribe_job_id: int | None = None
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


class TranscribeCreate(BaseModel):
    target_language: str | None = None


class DubCreate(BaseModel):
    target_language: str | None = None
    replace_existing: bool = False
    mix_mode: Literal["background_preserved", "voiceover_preview"] = "background_preserved"
    speaker_voices: dict[str, str] = Field(default_factory=dict)


class VoiceCastSuggestionOut(BaseModel):
    speaker_id: str
    voice_style: str
    cue_indices: list[int] = Field(default_factory=list)
    confidence: float | None = None
    voice_model: str


class VoiceCastOut(BaseModel):
    provider_id: str
    model_id: str
    suggestions: list[VoiceCastSuggestionOut] = Field(default_factory=list)
    analysed_cue_count: int = 0
    metadata_used: dict[str, str | int] = Field(default_factory=dict)


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
    task_id: int | None = None
    job_kind: str = "translate"
    trigger_type: str = "manual"
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
    provider_id: str | None = None
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
    created_at: DateTimeOut
    started_at: DateTimeOut
    completed_at: DateTimeOut


class BatchJobsOut(BaseModel):
    jobs: list[JobOut] = Field(default_factory=list)
    created_count: int = 0
    reused_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)


class AutomationScanResult(BaseModel):
    ok: bool
    message: str | None = None
    created_count: int = 0
    reused_count: int = 0
    skipped_count: int = 0
    errors: list[str] = Field(default_factory=list)
    scanned_at: DateTimeOut = None
    enabled: bool = False


class AutomationStatusOut(BaseModel):
    enabled: bool
    scanner_running: bool
    last_scan_at: DateTimeOut = None
    next_scan_at: DateTimeOut = None
    last_result: AutomationScanResult | None = None


class JobActionOut(BaseModel):
    """A single job run treated as an action on the same media/episode."""

    id: int
    action: str
    status: str
    datetime: DateTimeOut
    duration_seconds: float | None = None
    message: str | None = None
    current: bool = False
    target_language: str | None = None
    kind: Literal["job", "task"] = "job"
    progress: float | None = None
    progress_detail: str | None = None
    related_job_id: int | None = None


class JobLogOut(BaseModel):
    job_id: int
    exists: bool
    path: str
    entry_count: int = 0
    content: str | None = None
    entries: list[dict[str, Any]] | None = None


class JobRequestLogOut(BaseModel):
    job_id: int
    index: int
    exists: bool = False
    entry: dict[str, Any] | None = None


class JobUsageExchangeOut(BaseModel):
    index: int
    ts: str | None = None
    model: str
    action: str
    attempt: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    cost_estimated: bool = False
    status_code: int | None = None
    ok: bool = False
    error: str | None = None


class JobUsageModelOut(BaseModel):
    model: str
    name: str | None = None
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    prompt_price_per_million: float | None = None
    completion_price_per_million: float | None = None


class JobUsageActionOut(BaseModel):
    action: str
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None


class JobUsageRelatedOut(BaseModel):
    id: int
    action: str
    status: str
    model: str
    datetime: DateTimeOut = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    current: bool = False


class JobUsageTotalsOut(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    blended_cost_per_million: float | None = None


class JobUsageOut(BaseModel):
    job_id: int
    media_title: str | None = None
    job_kind: str = "translate"
    model: str
    status: str
    log_exists: bool = False
    pricing_source: str = "none"
    totals: JobUsageTotalsOut
    by_model: list[JobUsageModelOut] = Field(default_factory=list)
    by_action: list[JobUsageActionOut] = Field(default_factory=list)
    exchanges: list[JobUsageExchangeOut] = Field(default_factory=list)
    related_actions: list[JobUsageRelatedOut] = Field(default_factory=list)


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
    planner_error: str | None = None


class AiRoutingOut(BaseModel):
    routing_strategy: str
    allow_paid_fallback: bool
    allow_free_fallback: bool
    allow_unknown_pricing: bool
    maximum_cost_per_job_usd: float | None = None
    monthly_budget_enabled: bool = False
    monthly_budget_amount_usd: float | None = None
    allow_manual_budget_override: bool = False
    openrouter_log_full_exchanges: bool = False
    openrouter_temperature: float = 0


class AiRoutingUpdate(BaseModel):
    routing_strategy: Literal["free_only", "paid_only", "free_first", "paid_first"] | None = None
    allow_paid_fallback: bool | None = None
    allow_free_fallback: bool | None = None
    allow_unknown_pricing: bool | None = None
    maximum_cost_per_job_usd: float | None = Field(default=None, ge=0)
    clear_maximum_cost_per_job: bool = False
    monthly_budget_enabled: bool | None = None
    monthly_budget_amount_usd: float | None = Field(default=None, ge=0)
    clear_monthly_budget_amount: bool = False
    allow_manual_budget_override: bool | None = None
    openrouter_api_key: str | None = None
    clear_openrouter_api_key: bool = False
    openrouter_log_full_exchanges: bool | None = None
    openrouter_temperature: float | None = Field(default=None, ge=0, le=2)


class AiModelPreferenceIn(BaseModel):
    model_id: str
    tier: Literal["free", "paid"] | None = None
    purpose: Literal["translation", "audio_analysis"] = "translation"
    enabled: bool = True


class AiModelPatch(BaseModel):
    enabled: bool | None = None
    tier: Literal["free", "paid"] | None = None


class AiModelReorderIn(BaseModel):
    tier: Literal["free", "paid"] | None = None
    purpose: Literal["translation", "audio_analysis"] = "translation"
    ordered_ids: list[int]


class AiModelTestIn(BaseModel):
    model_id: str


class AiBudgetOut(BaseModel):
    enabled: bool
    limit: float | None = None
    used: float = 0
    remaining: float | None = None
    reserved: float = 0
    percent_used: float | None = None
    allow_manual_override: bool = False


# --- Localization / media (v0.3) ---


class LanguageCatalogOut(BaseModel):
    code: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    region: str | None = None
    flag: str = "🏳️"


class MediaRefOut(BaseModel):
    id: int | None = None
    provider_id: str
    external_id: str
    media_type: Literal["movie", "series", "episode"]
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    path: str | None = None
    parent_external_id: str | None = None
    bazarr_movie_id: int | None = None
    bazarr_series_id: int | None = None
    bazarr_episode_id: int | None = None


class MediaItemOut(BaseModel):
    id: int
    provider_id: str
    external_id: str
    media_type: str
    title: str
    year: int | None = None
    path: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    bazarr_movie_id: int | None = None
    bazarr_series_id: int | None = None
    bazarr_episode_id: int | None = None
    parent_media_id: int | None = None
    created_at: DateTimeOut
    updated_at: DateTimeOut


class LanguageAvailabilityOut(BaseModel):
    language_code: str
    language_name: str | None = None
    available: bool = False
    task_status: str | None = None
    task_id: int | None = None
    task_substate: str | None = None


class MediaLocalizationOut(BaseModel):
    media_id: int
    capability: str = "subtitles"
    languages: list[LanguageAvailabilityOut] = Field(default_factory=list)
    can_transcribe: bool = False
    transcribe_reason: str | None = None
    can_dub: bool = False
    dub_reason: str | None = None


class LocalizationTaskCreate(BaseModel):
    target_language: str
    capability: str = "subtitles"


class MediaEnsureIn(BaseModel):
    """Upsert a media item from a search hit or candidate IDs."""

    provider_id: str = "bazarr"
    external_id: str | None = None
    media_type: Literal["movie", "series", "episode"] | None = None
    title: str | None = None
    year: int | None = None
    path: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    bazarr_movie_id: int | None = None
    bazarr_series_id: int | None = None
    bazarr_episode_id: int | None = None
    parent_external_id: str | None = None


class TaskAiSummaryOut(BaseModel):
    requests: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    provider_id: str | None = None
    model_id: str | None = None


class LocalizationTaskOut(BaseModel):
    id: int
    media_item_id: int
    media_title: str | None = None
    media_type: str | None = None
    media_year: int | None = None
    target_language_code: str
    target_language_name: str
    capability: str
    status: str
    substate: str | None = None
    origin: str
    priority: str
    requested_by: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: DateTimeOut
    started_at: DateTimeOut
    completed_at: DateTimeOut
    updated_at: DateTimeOut
    executions: list[JobOut] = Field(default_factory=list)
    ai: TaskAiSummaryOut | None = None
    progress_steps: list[dict[str, str]] = Field(default_factory=list)
    draft_subtitle_path: str | None = None


class GlossaryEntryIn(BaseModel):
    source: str
    target: str
    locked: bool = True


class GlossaryEntryOut(BaseModel):
    id: int
    source: str
    target: str
    locked: bool


class GlossaryOut(BaseModel):
    scope_key: str
    target_language: str
    entries: list[GlossaryEntryOut] = Field(default_factory=list)
