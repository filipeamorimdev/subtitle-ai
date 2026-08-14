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
  prompt_price_per_million: number
  completion_price_per_million: number
  context_length: number | null
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
