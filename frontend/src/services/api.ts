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
  AiModelsPayload,
  AiUsagePage,
  AiCosts,
  AiRouting,
  AiProviderInfo,
} from '../types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
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
  getHealth: () => request<Health>('/api/health'),
  getSettings: () => request<Settings>('/api/settings'),
  updateSettings: (payload: SettingsUpdate) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  testBazarr: () =>
    request<ConnectionTestResult>('/api/settings/test/bazarr', { method: 'POST' }),
  testOpenRouter: () =>
    request<ConnectionTestResult>('/api/settings/test/openrouter', { method: 'POST' }),
  clearJobs: (opts?: {
    job_kind?: 'translate' | 'extract' | 'request'
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
  getJob: (id: number) => request<Job>(`/api/jobs/${id}`),
  getJobLog: (id: number) => request<JobLog>(`/api/jobs/${id}/log`),
  getJobRequests: (id: number) => request<JobUsageExchange[]>(`/api/jobs/${id}/requests`),
  getJobRequestLog: (id: number, index: number) =>
    request<JobRequestLog>(`/api/jobs/${id}/requests/${index}`),
  getJobUsage: (id: number) => request<JobUsage>(`/api/jobs/${id}/usage`),
  retryJob: (id: number) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),
  cancelJob: (id: number) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryBazarrSync: (id: number) =>
    request<Job>(`/api/jobs/${id}/retry-bazarr-sync`, { method: 'POST' }),
  getAutomationStatus: () => request<AutomationStatus>('/api/automation/status'),
  runAutomationScan: () =>
    request<AutomationScanResult>('/api/automation/run', { method: 'POST' }),
  getAiOverview: (period = 'month') =>
    request<AiOverview>(`/api/ai/overview?period=${encodeURIComponent(period)}`),
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
  addAiModel: (model_id: string, tier: 'free' | 'paid') =>
    request<{ id: number }>(`/api/ai/models`, {
      method: 'POST',
      body: JSON.stringify({ model_id, tier }),
    }),
  patchAiModel: (id: number, payload: { enabled?: boolean; tier?: 'free' | 'paid' }) =>
    request(`/api/ai/models/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAiModel: (id: number) => request(`/api/ai/models/${id}`, { method: 'DELETE' }),
  reorderAiModels: (tier: 'free' | 'paid', ordered_ids: number[]) =>
    request('/api/ai/models/reorder', {
      method: 'POST',
      body: JSON.stringify({ tier, ordered_ids }),
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
  listMedia: (limit = 100) => request<MediaItem[]>(`/api/media?limit=${limit}`),
  ensureMedia: (payload: Partial<MediaRef> & { external_id?: string }) =>
    request<MediaItem>('/api/media', { method: 'POST', body: JSON.stringify(payload) }),
  getMedia: (id: number) => request<MediaItem>(`/api/media/${id}`),
  getMediaLocalization: (id: number) =>
    request<MediaLocalization>(`/api/media/${id}/localization`),
  getMediaActions: (id: number) => request<JobAction[]>(`/api/media/${id}/actions`),
  createLocalizationTask: async (mediaId: number, payload: { target_language: string; capability?: string }) => {
    const response = await fetch(`/api/media/${mediaId}/localization-tasks`, {
      method: 'POST',
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
    const response = await fetch(`/api/localization-tasks${suffix}`)
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
}
