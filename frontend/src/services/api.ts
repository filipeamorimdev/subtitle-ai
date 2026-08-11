import type {
  BatchJobsResult,
  Candidate,
  ClearDataResult,
  ConnectionTestResult,
  GlossaryScope,
  GlossaryScopeCreate,
  GlossaryTerm,
  GlossaryTermCreate,
  GlossaryTermUpdate,
  GlossaryUniverse,
  Health,
  Job,
  JobAction,
  JobLog,
  JobUsage,
  OpenRouterModelsResult,
  Settings,
  SettingsUpdate,
  Stats,
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
  getOpenRouterModels: () => request<OpenRouterModelsResult>('/api/settings/openrouter/models'),
  clearJobs: (job_kind?: 'translate' | 'extract' | 'request') =>
    request<ClearDataResult>('/api/settings/clear/jobs', {
      method: 'POST',
      body: JSON.stringify(job_kind ? { job_kind } : {}),
    }),
  clearGlossaries: (kind?: 'universe' | 'series' | 'movie') =>
    request<ClearDataResult>('/api/settings/clear/glossaries', {
      method: 'POST',
      body: JSON.stringify(kind ? { kind } : {}),
    }),
  clearUsageStats: () =>
    request<ClearDataResult>('/api/settings/clear/usage', { method: 'POST' }),
  getCandidates: () => request<Candidate[]>('/api/candidates'),
  refreshCandidates: () =>
    request<Candidate[]>('/api/candidates/refresh', { method: 'POST' }),
  extractCandidate: (candidate_key: string) =>
    request<Job>('/api/candidates/extract', {
      method: 'POST',
      body: JSON.stringify({ candidate_key }),
    }),
  requestSubtitle: (candidate_key: string, language?: string) =>
    request<Job>('/api/candidates/request-subtitle', {
      method: 'POST',
      body: JSON.stringify({ candidate_key, language }),
    }),
  batchRequestSubtitles: () =>
    request<BatchJobsResult>('/api/candidates/batch/request-subtitle', { method: 'POST' }),
  batchExtract: () =>
    request<BatchJobsResult>('/api/candidates/batch/extract', { method: 'POST' }),
  batchTranslate: () =>
    request<BatchJobsResult>('/api/candidates/batch/translate', { method: 'POST' }),
  getJobs: (params?: { status?: string; limit?: number }) => {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.limit != null) query.set('limit', String(params.limit))
    const suffix = query.toString() ? `?${query}` : ''
    return request<Job[]>(`/api/jobs${suffix}`)
  },
  getJob: (id: number) => request<Job>(`/api/jobs/${id}`),
  getJobActions: (id: number) => request<JobAction[]>(`/api/jobs/${id}/actions`),
  getJobLog: (id: number) => request<JobLog>(`/api/jobs/${id}/log`),
  getJobUsage: (id: number) => request<JobUsage>(`/api/jobs/${id}/usage`),
  createJob: (payload: { candidate_key?: string; source_subtitle_path?: string; target_language?: string }) =>
    request<Job>('/api/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  retryJob: (id: number) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),
  cancelJob: (id: number) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryBazarrSync: (id: number) =>
    request<Job>(`/api/jobs/${id}/retry-bazarr-sync`, { method: 'POST' }),
  getStats: () => request<Stats>('/api/stats'),
  getGlossaryUniverses: () => request<GlossaryUniverse[]>('/api/glossary/universes'),
  getGlossaryScopes: (params?: { target_language?: string; kind?: string }) => {
    const query = new URLSearchParams()
    if (params?.target_language) query.set('target_language', params.target_language)
    if (params?.kind) query.set('kind', params.kind)
    const suffix = query.toString() ? `?${query}` : ''
    return request<GlossaryScope[]>(`/api/glossary/scopes${suffix}`)
  },
  getGlossaryScope: (id: number) => request<GlossaryScope>(`/api/glossary/scopes/${id}`),
  createGlossaryScope: (payload: GlossaryScopeCreate) =>
    request<GlossaryScope>('/api/glossary/scopes', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateGlossaryScope: (
    id: number,
    payload: { display_name?: string; parent_scope_id?: number | null; clear_parent?: boolean },
  ) =>
    request<GlossaryScope>(`/api/glossary/scopes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteGlossaryScope: (id: number) =>
    request<void>(`/api/glossary/scopes/${id}`, { method: 'DELETE' }),
  getGlossaryTerms: (scopeId: number, status?: string) => {
    const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
    return request<GlossaryTerm[]>(`/api/glossary/scopes/${scopeId}/terms${suffix}`)
  },
  createGlossaryTerm: (scopeId: number, payload: GlossaryTermCreate) =>
    request<GlossaryTerm>(`/api/glossary/scopes/${scopeId}/terms`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateGlossaryTerm: (termId: number, payload: GlossaryTermUpdate) =>
    request<GlossaryTerm>(`/api/glossary/terms/${termId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteGlossaryTerm: (termId: number) =>
    request<void>(`/api/glossary/terms/${termId}`, { method: 'DELETE' }),
  reviewGlossaryTerm: (termId: number, approve: boolean, lock = false) =>
    request<GlossaryTerm>(`/api/glossary/terms/${termId}/review`, {
      method: 'POST',
      body: JSON.stringify({ approve, lock }),
    }),
  getSuggestedGlossaryTerms: (target_language?: string) => {
    const suffix = target_language
      ? `?target_language=${encodeURIComponent(target_language)}`
      : ''
    return request<GlossaryTerm[]>(`/api/glossary/suggested${suffix}`)
  },
}
