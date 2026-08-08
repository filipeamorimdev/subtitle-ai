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
  media_roots?: string[]
  path_mappings?: PathMapping[]
  batch_size?: number
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
}

export interface Job {
  id: number
  candidate_key: string | null
  job_kind: 'translate' | 'extract' | string
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

export interface Stats {
  pending: number
  processing: number
  completed: number
  failed: number
  cancelled: number
  skipped: number
  total: number
}

export interface ConnectionTestResult {
  ok: boolean
  message: string
  details?: Record<string, unknown> | null
}
