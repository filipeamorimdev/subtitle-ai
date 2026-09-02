import type {
  AutomationScanResult,
  AutomationStatus,
  Candidate,
  ClearDataResult,
  ConnectionTestResult,
  Health,
  Job,
  JobAction,
  JobLog,
  JobRequestLog,
  JobUsage,
  JobUsageExchange,
  LanguageCatalogItem,
  LocalizationTask,
  MediaItem,
  MediaLocalization,
  MediaRef,
  Settings,
  SettingsUpdate,
  AiOverview,
  AiModelJobTimes,
  AiModelsPayload,
  AiUsagePage,
  AiCosts,
  AiRouting,
  AiProviderInfo,
  OperatorSession,
  OperatorSessionDetail,
  OperatorStatus,
  OperatorTurn,
  VoiceCast,
  VoiceLibrary,
  VoiceAudition,
  VoiceCharacter,
} from '../types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export const api = {
  getHealth: (live = false) => request<Health>(`/api/health${live ? '?live=1' : ''}`),
  getSettings: () => request<Settings>('/api/settings'),
  updateSettings: (payload: SettingsUpdate) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  testBazarr: () =>
    request<ConnectionTestResult>('/api/settings/test/bazarr', { method: 'POST' }),
  testJellyfin: () =>
    request<ConnectionTestResult>('/api/settings/test/jellyfin', { method: 'POST' }),
  testOpenRouter: () =>
    request<ConnectionTestResult>('/api/settings/test/openrouter', { method: 'POST' }),
  clearJobs: (opts?: {
    job_kind?: 'translate' | 'extract' | 'request' | 'transcribe' | 'dub'
    status?: 'failed' | 'skipped' | 'cancelled'
  }) =>
    request<ClearDataResult>('/api/settings/clear/jobs', {
      method: 'POST',
      body: JSON.stringify(opts ?? {}),
    }),
  clearUsageStats: () =>
    request<ClearDataResult>('/api/settings/clear/usage', { method: 'POST' }),
  getCandidates: () => request<Candidate[]>('/api/candidates'),
  refreshCandidates: () =>
    request<Candidate[]>('/api/candidates/refresh', { method: 'POST' }),
  getJobs: (params: {
    status?: string
    limit?: number
    sort?: 'created_at' | 'completed_at'
  } = {}) => {
    const query = new URLSearchParams()
    if (params.status) query.set('status', params.status)
    if (params.limit != null) query.set('limit', String(params.limit))
    if (params.sort) query.set('sort', params.sort)
    const suffix = query.toString() ? `?${query}` : ''
    return request<Job[]>(`/api/jobs${suffix}`)
  },
  getJob: (id: number) => request<Job>(`/api/jobs/${id}`),
  getJobLog: (id: number) => request<JobLog>(`/api/jobs/${id}/log`),
  getJobRequests: (id: number) => request<JobUsageExchange[]>(`/api/jobs/${id}/requests`),
  getJobRequestLog: (id: number, index: number) =>
    request<JobRequestLog>(`/api/jobs/${id}/requests/${index}`),
  getJobUsage: (id: number) => request<JobUsage>(`/api/jobs/${id}/usage`),
  retryJob: (id: number) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),
  cancelJob: (id: number) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  pauseJob: (id: number) => request<Job>(`/api/jobs/${id}/pause`, { method: 'POST' }),
  resumeJob: (id: number) => request<Job>(`/api/jobs/${id}/resume`, { method: 'POST' }),
  retryBazarrSync: (id: number) =>
    request<Job>(`/api/jobs/${id}/retry-bazarr-sync`, { method: 'POST' }),
  getAutomationStatus: () => request<AutomationStatus>('/api/automation/status'),
  runAutomationScan: () =>
    request<AutomationScanResult>('/api/automation/run', { method: 'POST' }),
  getAiOverview: (period = 'month') =>
    request<AiOverview>(`/api/ai/overview?period=${encodeURIComponent(period)}`),
  getAiModelJobTimes: (params: { period?: string; provider_id?: string; model_id: string }) => {
    const search = new URLSearchParams({ model_id: params.model_id })
    if (params.period) search.set('period', params.period)
    if (params.provider_id) search.set('provider_id', params.provider_id)
    return request<AiModelJobTimes>(`/api/ai/overview/job-times?${search}`)
  },
  getAiUsage: (params: Record<string, string | number | undefined> = {}) => {
    const search = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') search.set(key, String(value))
    })
    const suffix = search.toString() ? `?${search}` : ''
    return request<AiUsagePage>(`/api/ai/usage${suffix}`)
  },
  getAiCosts: (period = '30d', extra: { start?: string; end?: string } = {}) => {
    const search = new URLSearchParams({ period })
    if (extra.start) search.set('start', extra.start)
    if (extra.end) search.set('end', extra.end)
    return request<AiCosts>(`/api/ai/costs?${search}`)
  },
  getAiModels: () => request<AiModelsPayload>('/api/ai/models'),
  refreshAiModels: (provider_id = 'openrouter') =>
    request<{ ok: boolean; stale: boolean; message?: string; count: number; pricing_freshness?: string }>(
      `/api/ai/models/refresh?provider_id=${encodeURIComponent(provider_id)}`,
      { method: 'POST' },
    ),
  testAiModel: (model_id: string) =>
    request<ConnectionTestResult>('/api/ai/models/test', {
      method: 'POST',
      body: JSON.stringify({ model_id }),
    }),
  addAiModel: (
    model_id: string,
    tier?: 'free' | 'paid',
    purpose: 'translation' | 'audio_analysis' = 'translation',
  ) =>
    request<{ id: number }>(`/api/ai/models`, {
      method: 'POST',
      body: JSON.stringify({ model_id, tier, purpose }),
    }),
  patchAiModel: (id: number, payload: { enabled?: boolean; tier?: 'free' | 'paid' }) =>
    request(`/api/ai/models/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAiModel: (id: number) => request(`/api/ai/models/${id}`, { method: 'DELETE' }),
  reorderAiModels: (
    tier: 'free' | 'paid' | null,
    ordered_ids: number[],
    purpose: 'translation' | 'audio_analysis' = 'translation',
  ) =>
    request('/api/ai/models/reorder', {
      method: 'POST',
      body: JSON.stringify({ tier, ordered_ids, purpose }),
    }),
  getAiRouting: () => request<AiRouting>('/api/ai/routing'),
  updateAiRouting: (payload: Partial<AiRouting> & {
    clear_maximum_cost_per_job?: boolean
    clear_monthly_budget_amount?: boolean
    openrouter_api_key?: string
    clear_openrouter_api_key?: boolean
    openrouter_log_full_exchanges?: boolean
    openrouter_temperature?: number
  }) =>
    request<AiRouting>('/api/ai/routing', { method: 'PUT', body: JSON.stringify(payload) }),
  getAiProviders: () => request<{ providers: AiProviderInfo[] }>('/api/ai/providers'),
  updateAiProvider: (
    provider_id: string,
    payload: {
      api_key?: string
      clear_api_key?: boolean
      base_url?: string
      clear_base_url?: boolean
      enabled?: boolean
      openrouter_log_full_exchanges?: boolean
      openrouter_temperature?: number
    },
  ) =>
    request(`/api/ai/providers/${encodeURIComponent(provider_id)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  testAiProvider: (provider_id: string, opts: { fresh?: boolean; model_id?: string } = {}) => {
    const search = new URLSearchParams()
    if (opts.fresh) search.set('fresh', 'true')
    if (opts.model_id) search.set('model_id', opts.model_id)
    const q = search.toString()
    return request<ConnectionTestResult>(
      `/api/ai/providers/${encodeURIComponent(provider_id)}/test${q ? `?${q}` : ''}`,
      { method: 'POST' },
    )
  },

  getLanguages: () => request<LanguageCatalogItem[]>('/api/languages'),
  searchMedia: (q: string) =>
    request<MediaRef[]>(`/api/media/search?q=${encodeURIComponent(q)}`),
  listMedia: (limit = 100, offset = 0) =>
    request<MediaItem[]>(`/api/media?limit=${limit}&offset=${offset}`),
  ensureMedia: (payload: Partial<MediaRef> & { external_id?: string }) =>
    request<MediaItem>('/api/media', { method: 'POST', body: JSON.stringify(payload) }),
  getMedia: (id: number) => request<MediaItem>(`/api/media/${id}`),
  getMediaLocalization: (id: number) =>
    request<MediaLocalization>(`/api/media/${id}/localization`),
  transcribeMedia: (id: number, payload?: { target_language?: string }) =>
    request<Job>(`/api/media/${id}/transcribe`, {
      method: 'POST',
      body: JSON.stringify(payload ?? {}),
    }),
  dubMedia: (
    id: number,
    payload?: {
      target_language?: string
      replace_existing?: boolean
      mix_mode?: 'background_preserved' | 'voiceover_preview'
      speaker_voices?: Record<string, string>
    },
  ) =>
    request<Job>(`/api/media/${id}/dub`, {
      method: 'POST',
      body: JSON.stringify(payload ?? {}),
    }),
  suggestDubVoiceCast: (
    id: number,
    target_language: string,
    mix_mode: 'background_preserved' | 'voiceover_preview' = 'background_preserved',
  ) =>
    request<VoiceCast>(`/api/media/${id}/dub/voice-cast`, {
      method: 'POST',
      body: JSON.stringify({ target_language, mix_mode }),
    }),
  getDubVoiceCast: (id: number, target_language: string) =>
    request<VoiceCast>(
      `/api/media/${id}/dub/voice-cast?target_language=${encodeURIComponent(target_language)}`,
    ),
  updateDubVoiceCast: (
    id: number,
    target_language: string,
    payload: Pick<VoiceCast, 'suggestions' | 'mix_mode'>,
  ) =>
    request<VoiceCast>(
      `/api/media/${id}/dub/voice-cast?target_language=${encodeURIComponent(target_language)}`,
      { method: 'PUT', body: JSON.stringify(payload) },
    ),
  requestDubFromVoiceCast: (id: number, target_language: string) =>
    request<Job>(
      `/api/media/${id}/dub/voice-cast/request?target_language=${encodeURIComponent(target_language)}`,
      { method: 'POST' },
    ),
  getVoiceLibrary: (id: number, target_language: string) =>
    request<VoiceLibrary>(
      `/api/media/${id}/voice-library?target_language=${encodeURIComponent(target_language)}`,
    ),
  analyseVoiceLibrary: (
    id: number,
    target_language: string,
    mix_mode: 'background_preserved' | 'voiceover_preview' = 'background_preserved',
  ) =>
    request<VoiceLibrary>(`/api/media/${id}/voice-library/analyse`, {
      method: 'POST',
      body: JSON.stringify({ target_language, mix_mode }),
    }),
  buildVoiceReferenceCandidates: (id: number, target_language: string) =>
    request<Array<{ character_key: string; display_name: string; cue_indices: number[]; relative_path: string }>>(
      `/api/media/${id}/voice-library/reference-candidates?target_language=${encodeURIComponent(target_language)}`,
      { method: 'POST' },
    ),
  adoptVoiceReference: (
    id: number,
    characterId: number,
    payload: { relative_path: string; variant?: string; source_cue_indices?: number[] },
  ) =>
    request(`/api/media/${id}/voice-library/characters/${characterId}/adopt-reference`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  auditionVoiceCharacter: (id: number, characterId: number, target_language: string, voice_model?: string) => {
    const params = new URLSearchParams({ target_language })
    if (voice_model) params.set('voice_model', voice_model)
    return request<VoiceAudition>(
      `/api/media/${id}/voice-library/characters/${characterId}/audition?${params.toString()}`,
      { method: 'POST' },
    )
  },
  approveVoiceCharacter: (
    id: number,
    characterId: number,
    payload: { reference_id: number; voice_model: string; cfg_weight?: number; synthesis_seed?: number },
  ) =>
    request<VoiceCharacter>(`/api/media/${id}/voice-library/characters/${characterId}/approve`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateVoiceCueAssignments: (
    id: number,
    target_language: string,
    assignments: Array<{ cue_index: number; character_id: number | null }>,
  ) =>
    request<VoiceLibrary>(
      `/api/media/${id}/voice-library/cues?target_language=${encodeURIComponent(target_language)}`,
      { method: 'PUT', body: JSON.stringify({ assignments }) },
    ),
  requestDubFromVoiceLibrary: (
    id: number,
    payload?: {
      target_language?: string
      replace_existing?: boolean
      mix_mode?: 'background_preserved' | 'voiceover_preview'
    },
  ) =>
    request<Job>(`/api/media/${id}/voice-library/request-dub`, {
      method: 'POST',
      body: JSON.stringify(payload ?? { replace_existing: true }),
    }),
  voiceLibraryAudioUrl: (id: number, relativePath: string) =>
    `/api/media/${id}/voice-library/audio?path=${encodeURIComponent(relativePath)}`,
  voiceLibraryAuditionUrl: (id: number, wavPath: string) =>
    `/api/media/${id}/voice-library/audition-audio?file=${encodeURIComponent(wavPath)}`,
  getMediaActions: (id: number) => request<JobAction[]>(`/api/media/${id}/actions`),
  createLocalizationTask: async (mediaId: number, payload: { target_language: string; capability?: string }) => {
    const response = await fetch(`/api/media/${mediaId}/localization-tasks`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const body = await response.json().catch(() => ({}))
    if (response.status === 409 && body?.error === 'active_task_exists') {
      const err = new Error(body.detail || 'Active task already exists') as Error & {
        code?: string
        taskId?: number
      }
      err.code = 'active_task_exists'
      err.taskId = body.task_id
      throw err
    }
    if (!response.ok) {
      const detail = body.detail || response.statusText
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    return body as LocalizationTask
  },
  getLocalizationTasks: (params?: {
    status?: string
    origin?: string
    capability?: string
    language?: string
    media_type?: string
    media_item_id?: number
    active_only?: boolean
    include_detail?: boolean
    limit?: number
    offset?: number
    sort?: 'created_at' | 'completed_at'
  }) => {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.origin) query.set('origin', params.origin)
    if (params?.capability) query.set('capability', params.capability)
    if (params?.language) query.set('language', params.language)
    if (params?.media_type) query.set('media_type', params.media_type)
    if (params?.media_item_id != null) query.set('media_item_id', String(params.media_item_id))
    if (params?.active_only) query.set('active_only', 'true')
    if (params?.include_detail) query.set('include_detail', 'true')
    if (params?.limit != null) query.set('limit', String(params.limit))
    if (params?.offset != null) query.set('offset', String(params.offset))
    if (params?.sort) query.set('sort', params.sort)
    const suffix = query.toString() ? `?${query}` : ''
    return request<LocalizationTask[]>(`/api/localization-tasks${suffix}`)
  },
  getLocalizationTasksPage: async (params?: {
    status?: string
    origin?: string
    capability?: string
    language?: string
    media_type?: string
    media_item_id?: number
    active_only?: boolean
    limit?: number
    offset?: number
    sort?: 'created_at' | 'completed_at'
  }) => {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.origin) query.set('origin', params.origin)
    if (params?.capability) query.set('capability', params.capability)
    if (params?.language) query.set('language', params.language)
    if (params?.media_type) query.set('media_type', params.media_type)
    if (params?.media_item_id != null) query.set('media_item_id', String(params.media_item_id))
    if (params?.active_only) query.set('active_only', 'true')
    if (params?.limit != null) query.set('limit', String(params.limit))
    if (params?.offset != null) query.set('offset', String(params.offset))
    if (params?.sort) query.set('sort', params.sort)
    const suffix = query.toString() ? `?${query}` : ''
    const response = await fetch(`/api/localization-tasks${suffix}`, { credentials: 'same-origin' })
    if (!response.ok) {
      let detail = response.statusText
      try {
        const body = await response.json()
        detail = body.detail || detail
      } catch {
        /* ignore */
      }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
    const items = (await response.json()) as LocalizationTask[]
    const total = Number(response.headers.get('X-Total-Count') || items.length)
    return { items, total }
  },
  getLocalizationTask: (id: number) =>
    request<LocalizationTask>(`/api/localization-tasks/${id}`),
  retryLocalizationTask: (id: number) =>
    request<LocalizationTask>(`/api/localization-tasks/${id}/retry`, { method: 'POST' }),
  cancelLocalizationTask: (id: number) =>
    request<LocalizationTask>(`/api/localization-tasks/${id}/cancel`, { method: 'POST' }),
  getMediaGlossary: (mediaId: number, language: string) =>
    request<{
      scope_key: string
      target_language: string
      entries: { id: number; source: string; target: string; locked: boolean }[]
    }>(`/api/media/${mediaId}/glossary?language=${encodeURIComponent(language)}`),
  putMediaGlossary: (
    mediaId: number,
    language: string,
    entries: { source: string; target: string; locked?: boolean }[],
  ) =>
    request(`/api/media/${mediaId}/glossary?language=${encodeURIComponent(language)}`, {
      method: 'PUT',
      body: JSON.stringify(entries),
    }),
  exportSettings: () => request<{ settings: Settings; secrets_omitted: boolean }>('/api/settings/export'),
  importSettings: (payload: SettingsUpdate) =>
    request<Settings>('/api/settings/import', { method: 'POST', body: JSON.stringify(payload) }),
  getOperatorStatus: () => request<OperatorStatus>('/api/operator/status'),
  createOperatorSession: () =>
    request<OperatorSession>('/api/operator/sessions', { method: 'POST' }),
  getOperatorSession: (id: number) =>
    request<OperatorSessionDetail>(`/api/operator/sessions/${id}`),
  postOperatorMessage: (
    id: number,
    payload: {
      content?: string
      confirmed_tool?: { name: string; arguments?: Record<string, unknown> }
    },
  ) =>
    request<OperatorTurn>(`/api/operator/sessions/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
