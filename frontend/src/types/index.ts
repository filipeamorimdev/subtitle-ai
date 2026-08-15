export interface Language {
  code: string
  name: string
}

export interface PathMapping {
  bazarr_prefix: string
  local_prefix: string
}

export interface Settings {
  bazarr_url: string | null
  bazarr_api_key_masked: string | null
  bazarr_api_key_configured: boolean
  openrouter_api_key_masked: string | null
  openrouter_api_key_configured: boolean
  openrouter_model: string
  target_language: Language
  source_languages: string[]
  media_roots: string[]
  path_mappings: PathMapping[]
  batch_size: number
  max_concurrent_translate: number
  max_concurrent_extract: number
  max_concurrent_request: number
  automatic_fallback_enabled: boolean
  automatic_scan_interval_minutes: number
  bazarr_grace_period_minutes: number
  automatic_retry_enabled: boolean
  maximum_automatic_retries: number
  openrouter_log_full_exchanges: boolean
  routing_strategy: string
  allow_paid_fallback: boolean
  allow_free_fallback: boolean
  allow_unknown_pricing: boolean
  maximum_cost_per_job_usd: number | null
  monthly_budget_enabled: boolean
  monthly_budget_amount_usd: number | null
  allow_manual_budget_override: boolean
}

export interface SettingsUpdate {
  bazarr_url?: string | null
  bazarr_api_key?: string | null
  clear_bazarr_api_key?: boolean
  openrouter_api_key?: string | null
  clear_openrouter_api_key?: boolean
  openrouter_model?: string
  target_language_code?: string
  target_language_name?: string
  source_languages?: string[]
  path_mappings?: PathMapping[]
  batch_size?: number
  max_concurrent_translate?: number
  max_concurrent_extract?: number
  max_concurrent_request?: number
  automatic_fallback_enabled?: boolean
  automatic_scan_interval_minutes?: number
  bazarr_grace_period_minutes?: number
  automatic_retry_enabled?: boolean
  maximum_automatic_retries?: number
  openrouter_log_full_exchanges?: boolean
  routing_strategy?: string
  allow_paid_fallback?: boolean
  allow_free_fallback?: boolean
  allow_unknown_pricing?: boolean
  maximum_cost_per_job_usd?: number | null
  clear_maximum_cost_per_job?: boolean
  monthly_budget_enabled?: boolean
  monthly_budget_amount_usd?: number | null
  clear_monthly_budget_amount?: boolean
  allow_manual_budget_override?: boolean
}

export interface EmbeddedSubtitle {
  language: string | null
  codec: string | null
  kind: 'text' | 'image' | 'unknown'
  extractable: boolean
  stream_index: number | null
  hi: boolean
  forced: boolean
  title: string | null
  source: string
  label: string
}

export interface Candidate {
  key: string
  media_type: 'movie' | 'episode'
  title: string
  media_path: string
  bazarr_movie_id: number | null
  bazarr_episode_id: number | null
  bazarr_series_id: number | null
  target_language: string
  source_language: string | null
  source_subtitle_path: string | null
  target_subtitle_path: string | null
  can_translate: boolean
  reason_code: string | null
  reason: string | null
  embedded_subtitles: EmbeddedSubtitle[]
  has_embedded: boolean
  can_extract: boolean
  extract_stream_index: number | null
  extract_language: string | null
  active_extract_job_id: number | null
  active_request_job_id: number | null
  active_translate_job_id: number | null
  latest_job_id: number | null
}

export interface Job {
  id: number
  candidate_key: string | null
  job_kind: 'translate' | 'extract' | string
  trigger_type?: 'manual' | 'automatic' | string
  media_type: string
  media_path: string
  media_title: string | null
  source_subtitle_path: string
  target_subtitle_path: string
  source_language: string
  target_language: string
  model: string
  status: string
  progress: number
  progress_detail: string | null
  error: string | null
  warning: string | null
  reason_code: string | null
  extract_stream_index: number | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  created_at: string | null
  started_at: string | null
  completed_at: string | null
}

export interface JobAction {
  id: number
  action: string
  status: string
  datetime: string | null
  duration_seconds: number | null
  message: string | null
  current: boolean
}

export interface JobLog {
  job_id: number
  exists: boolean
  path: string
  entry_count: number
  content: string | null
  entries: Record<string, unknown>[] | null
}

export interface JobUsageExchange {
  index: number
  ts: string | null
  model: string
  action: string
  attempt: number | null
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number | null
  cost_estimated: boolean
  status_code: number | null
  ok: boolean
  error: string | null
}

export interface JobUsageModel {
  model: string
  name: string | null
  requests: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number | null
  prompt_price_per_million: number | null
  completion_price_per_million: number | null
}

export interface JobUsageActionKind {
  action: string
  requests: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number | null
}

export interface JobUsageRelated {
  id: number
  action: string
  status: string
  model: string
  datetime: string | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cost_usd: number | null
  current: boolean
}

export interface JobUsageTotals {
  requests: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number | null
  blended_cost_per_million: number | null
}

export interface JobUsage {
  job_id: number
  media_title: string | null
  job_kind: string
  model: string
  status: string
  log_exists: boolean
  pricing_source: string
  totals: JobUsageTotals
  by_model: JobUsageModel[]
  by_action: JobUsageActionKind[]
  exchanges: JobUsageExchange[]
  related_actions: JobUsageRelated[]
}

export interface BatchJobsResult {
  jobs: Job[]
  created_count: number
  reused_count: number
  skipped_count: number
  errors: string[]
}

export interface Stats {
  pending: number
  processing: number
  completed: number
  failed: number
  cancelled: number
  skipped: number
  total: number
}

export interface AutomationScanResult {
  ok: boolean
  message: string | null
  created_count: number
  reused_count: number
  skipped_count: number
  errors: string[]
  scanned_at: string | null
  enabled: boolean
}

export interface AutomationStatus {
  enabled: boolean
  scanner_running: boolean
  last_scan_at: string | null
  next_scan_at: string | null
  last_result: AutomationScanResult | null
}

export interface Health {
  status: string
  version: string
  database: string
  bazarr: string
  openrouter: string
}

export interface ConnectionTestResult {
  ok: boolean
  message: string
  details?: Record<string, unknown> | null
}

export interface ClearDataResult {
  deleted: number
  message: string
  details?: Record<string, unknown> | null
}

export interface OpenRouterModel {
  id: string
  name: string
  prompt_price_per_million: number | null
  completion_price_per_million: number | null
  context_length: number | null
  pricing_tier?: string | null
  description?: string | null
  compatible?: boolean | null
  compatibility_reason?: string | null
  stale?: boolean | null
  unavailable?: boolean | null
  input_modalities?: string[] | null
  output_modalities?: string[] | null
}

export interface OpenRouterModelsResult {
  models: OpenRouterModel[]
}

export interface GlossaryUniverse {
  key: string
  display_name: string
}

export interface GlossaryScope {
  id: number
  kind: 'universe' | 'series' | 'movie' | string
  key: string
  display_name: string
  target_language: string
  parent_scope_id: number | null
  bazarr_series_id: number | null
  bazarr_movie_id: number | null
  term_count: number
  suggested_count: number
  created_at: string | null
  updated_at: string | null
}

export interface GlossaryTerm {
  id: number
  scope_id: number
  source: string
  target: string
  term_type: string
  policy: string
  status: 'active' | 'suggested' | 'rejected' | string
  locked: boolean
  source_origin: string
  notes: string | null
  scope_kind: string | null
  scope_name: string | null
  created_at: string | null
  updated_at: string | null
}

export interface GlossaryScopeCreate {
  kind: 'universe' | 'series' | 'movie'
  key: string
  display_name: string
  target_language: string
  parent_scope_id?: number | null
}

export interface GlossaryTermCreate {
  source: string
  target: string
  term_type?: string
  policy?: string
  status?: string
  locked?: boolean
  notes?: string | null
}

export interface GlossaryTermUpdate {
  source?: string
  target?: string
  term_type?: string
  policy?: string
  status?: string
  locked?: boolean
  notes?: string | null
}

export interface AiRouting {
  routing_strategy: string
  allow_paid_fallback: boolean
  allow_free_fallback: boolean
  allow_unknown_pricing: boolean
  maximum_cost_per_job_usd: number | null
  monthly_budget_enabled: boolean
  monthly_budget_amount_usd: number | null
  allow_manual_budget_override: boolean
  openrouter_log_full_exchanges: boolean
}

export interface AiPreference {
  id: number
  model_id: string
  tier: string
  priority: number
  enabled: boolean
  name?: string
  pricing_tier?: string
  prompt_price_per_million?: number | null
  completion_price_per_million?: number | null
  context_length?: number | null
  compatible?: boolean
  compatibility_reason?: string
  available?: boolean
  unavailable?: boolean
  stale?: boolean
  configured_priority?: number
  adaptive_rank?: number | null
  adaptive_score?: number | null
  confidence?: string
  sample_count?: number
  clean_success_rate?: number | null
  repair_rate?: number | null
  average_cost_per_clean_success_usd?: number | null
  average_latency_ms?: number | null
  last_used_at?: string | null
}

export interface AiModelsPayload {
  openrouter_configured: boolean
  openrouter_api_key_masked: string | null
  catalog_fetched_at: string | null
  catalog_stale: boolean
  catalog_age_seconds: number | null
  preferences: AiPreference[]
  catalog: OpenRouterModel[]
  routing: AiRouting
}

export interface AiOverview {
  period: string
  empty: boolean
  status?: 'healthy' | 'idle' | 'attention'
  status_reasons?: string[]
  active_jobs?: number
  cost: { current: number; previous: number | null }
  requests: number
  success_rate: number | null
  clean_success_rate?: number | null
  repair_rate?: number | null
  validation_failure_rate?: number | null
  technical_failure_rate?: number | null
  tokens: { input: number; output: number; total: number }
  free_requests: number
  paid_requests: number
  free_tokens: number
  paid_tokens: number
  paid_cost_usd: number
  average_cost_usd: number | null
  average_latency_ms: number | null
  cards: Record<string, { cost_usd: number; requests: number; clean_success_rate?: number | null }>
  budget: {
    enabled: boolean
    limit: number | null
    used: number
    remaining: number | null
    reserved: number
    percent_used: number | null
    allow_manual_override: boolean
  }
  ai_summary?: {
    this_month_cost_usd: number
    this_month_requests: number
    clean_success_rate: number | null
    budget_percent_used: number | null
    best_model_id: string | null
    status: string
  }
  ranking: Array<{
    model_id: string
    configured_priority?: number | null
    adaptive_rank: number | null
    adaptive_score: number | null
    quality_score: number | null
    cost_score: number | null
    speed_score: number | null
    reliability_score: number | null
    clean_success_rate: number | null
    repair_rate: number | null
    validation_failure_rate?: number | null
    technical_failure_rate?: number | null
    average_cost_per_clean_success_usd: number | null
    average_latency_ms: number | null
    sample_count: number
    confidence: string
    last_used_at: string | null
  }>
  routing: Array<{
    id: number
    created_at: string | null
    job_id: number | null
    event: string
    strategy: string | null
    model_id: string | null
    next_model_id: string | null
    failure_category: string | null
    detail: string | null
  }>
}

export interface AiUsagePage {
  total: number
  offset: number
  limit: number
  items: Array<{
    id: number
    created_at: string | null
    job_id: number | null
    media_title: string | null
    operation_type: string
    model_id: string
    tier: string
    trigger_type: string
    input_tokens: number
    output_tokens: number
    total_tokens: number
    cost_usd: number | null
    status: string
    failure_category: string | null
    outcome: string | null
    latency_ms: number | null
  }>
  by_model: Array<{
    model_id: string
    requests: number
    successes: number
    failures: number
    success_rate: number
    input_tokens: number
    output_tokens: number
    total_tokens: number
    cost_usd: number
    average_latency_ms: number | null
    clean_success_rate: number
    repair_rate: number
    validation_failure_rate: number
    technical_failure_rate: number
  }>
  totals?: {
    requests: number
    successful_requests: number
    failed_requests: number
    success_rate: number | null
    total_tokens: number
    cost_usd: number
    clean_success_rate: number | null
    repair_rate: number | null
    validation_failure_rate: number | null
    technical_failure_rate: number | null
    average_latency_ms: number | null
  }
}

export interface AiCosts {
  period: string
  series: Array<{ date: string; cost_usd: number; request_count?: number }>
  by_model: Array<{ model_id: string; requests: number; cost_usd: number; tokens: number }>
  failure_categories?: Array<{ category: string; count: number }>
  free_vs_paid: {
    free_requests: number
    paid_requests: number
    free_cost_usd: number
    paid_cost_usd: number
  }
}
