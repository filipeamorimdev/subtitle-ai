import type { Job, LocalizationTask } from '../types'

const OPEN_JOB_STATUSES = new Set(['pending', 'processing', 'paused'])

export function isOpenJobStatus(status?: string | null) {
  return Boolean(status && OPEN_JOB_STATUSES.has(status))
}

export function latestActiveJob(task: LocalizationTask): Job | null {
  const jobs = task.executions || []
  return (
    [...jobs].reverse().find((item) => isOpenJobStatus(item.status)) ||
    jobs[jobs.length - 1] ||
    null
  )
}

export function taskProgressPct(task: LocalizationTask) {
  if (task.status === 'waiting_for_source') return 0
  const jobs = task.executions || []
  const active = [...jobs].reverse().find((item) => isOpenJobStatus(item.status))
  if (active) return Math.round(Math.min(100, Math.max(0, active.progress ?? 0)))
  if (task.status === 'verifying') return 100
  const job = latestActiveJob(task)
  return Math.round(Math.min(100, Math.max(0, job?.progress ?? 0)))
}

export function taskElapsedStart(task: LocalizationTask) {
  const job = latestActiveJob(task)
  return job?.started_at || task.started_at || job?.created_at || task.created_at
}
