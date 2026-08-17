const TASK_STATUS_LABELS: Record<string, string> = {
  requested: 'Requested',
  planning: 'Planning',
  waiting_for_source: 'Waiting for source',
  processing: 'Processing',
  verifying: 'Verifying',
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
])

export function isActiveTaskStatus(status: string) {
  return ACTIVE_TASK_STATUSES.has(status)
}

export function taskStatusLabel(status: string, substate?: string | null) {
  if (status === 'processing') {
    if (substate === 'extracting_source') return 'Extracting'
    if (substate === 'discovering_source') return 'Finding source'
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
  return ['failed', 'blocked', 'cancelled', 'verifying', 'waiting_for_source'].includes(status)
}

export function canCancelTask(status: string) {
  return isActiveTaskStatus(status)
}

export function canRetryJob(status: string) {
  return status === 'failed' || status === 'skipped' || status === 'cancelled'
}

export function jobStatusClass(status: string) {
  if (status === 'completed') return 'text-emerald-700 dark:text-emerald-300'
  if (status === 'failed') return 'text-red-700 dark:text-red-300'
  if (status === 'cancelled' || status === 'skipped') return 'text-amber-700 dark:text-amber-300'
  if (status === 'processing') return 'text-accent'
  return 'text-ink-700 dark:text-ink-200'
}

export function languageChipClass(status: string | null, available: boolean) {
  if (status === 'failed') {
    return 'border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200'
  }
  if (status && isActiveTaskStatus(status)) {
    return 'border-accent/40 bg-accent/10 text-accent'
  }
  if (available || status === 'completed') {
    return 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200'
  }
  if (status === 'cancelled' || status === 'blocked') {
    return 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
  }
  return 'border-ink-200 bg-ink-50 text-ink-700 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-200'
}
