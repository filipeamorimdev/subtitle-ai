import type {
  BatchJobsResult,
  Candidate,
  ConnectionTestResult,
  Job,
  JobLog,
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
  getSettings: () => request<Settings>('/api/settings'),
  updateSettings: (payload: SettingsUpdate) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  testBazarr: () =>
    request<ConnectionTestResult>('/api/settings/test/bazarr', { method: 'POST' }),
  testOpenRouter: () =>
    request<ConnectionTestResult>('/api/settings/test/openrouter', { method: 'POST' }),
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
  batchExtractAndTranslate: () =>
    request<BatchJobsResult>('/api/candidates/batch/extract-and-translate', {
      method: 'POST',
    }),
  getJobs: () => request<Job[]>('/api/jobs'),
  getJob: (id: number) => request<Job>(`/api/jobs/${id}`),
  getJobLog: (id: number) => request<JobLog>(`/api/jobs/${id}/log`),
  createJob: (payload: { candidate_key?: string; source_subtitle_path?: string; target_language?: string }) =>
    request<Job>('/api/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  retryJob: (id: number) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),
  cancelJob: (id: number) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryBazarrSync: (id: number) =>
    request<Job>(`/api/jobs/${id}/retry-bazarr-sync`, { method: 'POST' }),
  getStats: () => request<Stats>('/api/stats'),
}
