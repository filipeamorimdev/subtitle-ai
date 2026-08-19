import type { Job, LocalizationTask } from '../types'

export function latestActiveJob(task: LocalizationTask): Job | null {
  const jobs = task.executions || []
  return (
    [...jobs].reverse().find((item) => item.status === 'pending' || item.status === 'processing') ||
    jobs[jobs.length - 1] ||
    null
  )
}

export function taskProgressPct(task: LocalizationTask) {
  if (task.status === 'waiting_for_source') return 0
  if (task.status === 'awaiting_approval') return 100
  const jobs = task.executions || []
  const active = [...jobs]
    .reverse()
    .find((item) => item.status === 'pending' || item.status === 'processing')
  if (active) return Math.round(Math.min(100, Math.max(0, active.progress ?? 0)))
  if (task.status === 'verifying') return 100
  const job = latestActiveJob(task)
  return Math.round(Math.min(100, Math.max(0, job?.progress ?? 0)))
}

export function taskElapsedStart(task: LocalizationTask) {
  const job = latestActiveJob(task)
  return job?.started_at || task.started_at || job?.created_at || task.created_at
}
