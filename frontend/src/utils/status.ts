import type { LocalizationTask } from '../types'

const TASK_STATUS_LABELS: Record<string, string> = {
  requested: 'Requested',
  planning: 'Planning',
  waiting_for_source: 'Waiting for source',
  processing: 'Processing',
  verifying: 'Verifying',
  awaiting_approval: 'Awaiting approval',
  completed: 'Completed',
  failed: 'Failed',
  blocked: 'Blocked',
  cancelled: 'Cancelled',
}

const ACTIVE_TASK_STATUSES = new Set([
  'requested',
  'planning',
  'waiting_for_source',
  'processing',
  'verifying',
  'awaiting_approval',
])

export function isActiveTaskStatus(status: string) {
  return ACTIVE_TASK_STATUSES.has(status)
}

type LocalizationTarget = Pick<
  LocalizationTask,
  'id' | 'media_item_id' | 'target_language_code' | 'capability' | 'status'
>

export function latestTasksByLanguage<T extends { id: number; target_language_code: string }>(
  tasks: T[],
): Map<string, T> {
  const map = new Map<string, T>()
  for (const task of tasks) {
    const prev = map.get(task.target_language_code)
    if (!prev || task.id > prev.id) map.set(task.target_language_code, task)
  }
  return map
}

/** Prefer latest task per language + capability so dub and subtitle chips coexist. */
export function latestTasksByLanguageCapability<
  T extends { id: number; target_language_code: string; capability?: string },
>(tasks: T[]): Map<string, T> {
  const map = new Map<string, T>()
  for (const task of tasks) {
    const key = `${task.target_language_code}:${task.capability || 'subtitles'}`
    const prev = map.get(key)
    if (!prev || task.id > prev.id) map.set(key, task)
  }
  return map
}

export type PipelineStage =
  | 'requesting'
  | 'extracting'
  | 'transcribing'
  | 'translating'
  | 'verifying'
  | 'approval'
  | 'dubbing'
  | 'dub_blocked'
  | 'failed'
  | 'other'

type PipelineTask = Pick<LocalizationTask, 'status' | 'substate' | 'capability' | 'error_code'> & {
  executions?: { job_kind?: string; status: string }[]
}

function activeJobKind(task: PipelineTask): string | null {
  const open = new Set(['pending', 'processing', 'paused'])
  const jobs = task.executions || []
  const active = [...jobs].reverse().find((job) => open.has(job.status))
  return active?.job_kind || null
}

/** Classify a localization task into a dashboard pipeline stage. */
export function pipelineStage(task: PipelineTask): PipelineStage {
  const capability = (task.capability || 'subtitles').toLowerCase()
  const status = task.status
  const sub = task.substate || ''
  const kind = (activeJobKind(task) || '').toLowerCase()

  if (capability === 'audio') {
    if (status === 'failed') return 'failed'
    if (status === 'blocked' && (sub === 'awaiting_subtitles' || task.error_code === 'subtitle_missing')) {
      return 'dub_blocked'
    }
    if (
      isActiveTaskStatus(status) ||
      status === 'planning' ||
      sub === 'dubbing' ||
      kind === 'dub'
    ) {
      return 'dubbing'
    }
    return 'other'
  }

  if (status === 'failed') return 'failed'
  if (status === 'awaiting_approval') return 'approval'
  if (status === 'verifying') return 'verifying'

  if (
    status === 'requested' ||
    status === 'waiting_for_source' ||
    sub === 'discovering_source' ||
    sub === 'awaiting_source' ||
    sub === 'source_cooldown' ||
    kind === 'request'
  ) {
    return 'requesting'
  }

  if (sub === 'extracting_source' || kind === 'extract') return 'extracting'
  if (sub === 'transcribing_source' || kind === 'transcribe') return 'transcribing'
  if (sub === 'translating' || kind === 'translate') return 'translating'
  if (status === 'processing' || status === 'planning') return 'other'
  return 'other'
}

/** True when this failed task is still the latest attempt for that media + language. */
export function isUnresolvedFailedTask(task: LocalizationTarget, allTasks: LocalizationTarget[]) {
  if (task.status !== 'failed') return false
  const same = allTasks.filter(
    (other) =>
      other.media_item_id === task.media_item_id &&
      other.target_language_code === task.target_language_code &&
      (other.capability || 'subtitles') === (task.capability || 'subtitles'),
  )
  const latest = same.reduce((best, other) => (other.id > best.id ? other : best))
  return latest.id === task.id
}

export function taskStatusLabel(status: string, substate?: string | null) {
  if (status === 'processing') {
    if (substate === 'extracting_source') return 'Extracting'
    if (substate === 'discovering_source') return 'Finding source'
    if (substate === 'transcribing_source') return 'Transcribing'
    if (substate === 'dubbing') return 'Dubbing'
    return 'Translating'
  }
  return TASK_STATUS_LABELS[status] || status.replaceAll('_', ' ')
}

export function taskStatusIcon(status: string) {
  if (status === 'completed') return '✓'
  if (status === 'failed') return '✗'
  if (status === 'waiting_for_source') return '…'
  if (isActiveTaskStatus(status)) return '⟳'
  return '—'
}

export function canRetryTask(status: string) {
  return ['failed', 'blocked', 'cancelled', 'waiting_for_source', 'awaiting_approval'].includes(status)
}

export function canApproveTask(status: string) {
  return status === 'awaiting_approval'
}

const BAZARR_VERIFY_FAIL_CODES = new Set(['bazarr_verify_failed', 'bazarr_rescan_failed'])

export function isBazarrVerifyFailCode(code?: string | null) {
  return Boolean(code && BAZARR_VERIFY_FAIL_CODES.has(code))
}

type BazarrSyncRetryTask = {
  status: string
  error_code?: string | null
  progress_steps?: { id: string; state: string }[]
  executions?: {
    id: number
    job_kind?: string
    status: string
    reason_code?: string | null
  }[]
}

function latestTranslateJob(executions: BazarrSyncRetryTask['executions']) {
  return [...(executions || [])]
    .filter((job) => (job.job_kind || 'translate') === 'translate')
    .sort((a, b) => b.id - a.id)[0]
}

export function canRetryBazarrSync(task: BazarrSyncRetryTask) {
  if (task.status !== 'verifying') return false
  if (isBazarrVerifyFailCode(task.error_code)) return true
  if ((task.progress_steps || []).some((step) => step.id === 'verify' && step.state === 'failed')) {
    return true
  }
  const latest = latestTranslateJob(task.executions)
  if (!latest || latest.status !== 'completed') return false
  return isBazarrVerifyFailCode(latest.reason_code)
}

export function canCancelTask(status: string) {
  return isActiveTaskStatus(status)
}

export function canPauseJob(status?: string | null) {
  return status === 'pending'
}

export function canResumeJob(status?: string | null) {
  return status === 'paused'
}

export function canRetryJob(status: string) {
  return status === 'failed' || status === 'skipped' || status === 'cancelled'
}

/** Job kinds that write OpenRouter logs and AI usage records. */
const AI_JOB_KINDS = new Set(['translate'])

export function jobHasAiArtifacts(jobKind?: string | null) {
  return AI_JOB_KINDS.has((jobKind || 'translate').toLowerCase())
}

export function jobKindLabel(jobKind?: string | null) {
  const kind = (jobKind || 'translate').toLowerCase()
  const labels: Record<string, string> = {
    translate: 'Translate',
    extract: 'Extract',
    request: 'Request',
    transcribe: 'Transcribe',
    dub: 'Dub',
  }
  return labels[kind] || kind
}

export function jobStatusClass(status: string) {
  if (status === 'completed') return 'text-emerald-700 dark:text-emerald-300'
  if (status === 'failed') return 'text-red-700 dark:text-red-300'
  if (status === 'cancelled') return 'text-orange-800 dark:text-orange-300'
  if (status === 'paused') return 'text-accent'
  if (status === 'skipped' || status === 'blocked') {
    return 'text-amber-700 dark:text-amber-300'
  }
  if (
    status === 'processing' ||
    status === 'waiting_for_source' ||
    status === 'planning' ||
    status === 'requested' ||
    status === 'verifying' ||
    status === 'awaiting_approval'
  ) {
    return 'text-accent'
  }
  return 'text-ink-700 dark:text-ink-200'
}

export function jobStatusBadgeClass(status: string) {
  if (status === 'completed') {
    return 'inline-flex rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs font-semibold capitalize text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200'
  }
  if (status === 'failed') {
    return 'inline-flex rounded-full border border-red-300 bg-red-50 px-2 py-0.5 text-xs font-semibold capitalize text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200'
  }
  if (status === 'cancelled') {
    return 'inline-flex rounded-full border border-orange-300 bg-orange-500 px-2 py-0.5 text-xs font-semibold capitalize text-white dark:border-orange-700 dark:bg-orange-600 dark:text-white'
  }
  if (status === 'skipped' || status === 'blocked') {
    return 'inline-flex rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-semibold capitalize text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
  }
  if (
    status === 'processing' ||
    status === 'waiting_for_source' ||
    status === 'planning' ||
    status === 'requested' ||
    status === 'verifying' ||
    status === 'awaiting_approval' ||
    status === 'pending' ||
    status === 'paused'
  ) {
    return 'inline-flex rounded-full border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs font-semibold capitalize text-accent'
  }
  return 'inline-flex rounded-full border border-ink-200 bg-ink-50 px-2 py-0.5 text-xs font-semibold capitalize text-ink-700 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-200'
}

export function languageChipClass(
  status: string | null,
  available: boolean,
  opts?: { verificationFailed?: boolean },
) {
  if (opts?.verificationFailed) {
    return 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
  }
  if (status === 'failed') {
    return 'border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200'
  }
  if (status === 'cancelled') {
    return 'border-orange-300 bg-orange-500 text-white dark:border-orange-700 dark:bg-orange-600 dark:text-white'
  }
  if (status === 'blocked') {
    return 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
  }
  if (status && isActiveTaskStatus(status)) {
    return 'border-accent/40 bg-accent/10 text-accent'
  }
  if (available || status === 'completed') {
    return 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200'
  }
  return 'border-ink-200 bg-ink-50 text-ink-700 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-200'
}
