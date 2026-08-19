import { describe, expect, it } from 'vitest'
import { canRetryTask, isActiveTaskStatus, isUnresolvedFailedTask, latestTasksByLanguage, taskStatusLabel } from './status'
import { latestActiveJob, taskElapsedStart, taskProgressPct } from './taskProgress'
import type { LocalizationTask } from '../types'

function task(partial: Partial<LocalizationTask>): LocalizationTask {
  return {
    id: 1,
    media_item_id: 1,
    media_title: 'Example',
    media_type: 'movie',
    media_year: 2026,
    target_language_code: 'pt-PT',
    target_language_name: 'Portuguese (Portugal)',
    capability: 'subtitles',
    status: 'processing',
    substate: null,
    origin: 'manual',
    priority: 'high',
    requested_by: null,
    error_code: null,
    error_message: null,
    created_at: null,
    started_at: null,
    completed_at: null,
    updated_at: null,
    executions: [],
    ai: null,
    progress_steps: [],
    ...partial,
  }
}

describe('task status helpers', () => {
  it('labels processing substates', () => {
    expect(taskStatusLabel('processing', 'extracting_source')).toBe('Extracting')
    expect(taskStatusLabel('awaiting_approval')).toBe('Awaiting approval')
    expect(isActiveTaskStatus('awaiting_approval')).toBe(true)
    expect(canRetryTask('awaiting_approval')).toBe(true)
  })

  it('hides a failed task after a later successful localization', () => {
    const failed = task({ id: 1, status: 'failed', media_item_id: 27 })
    const completed = task({ id: 2, status: 'completed', media_item_id: 27 })
    expect(isUnresolvedFailedTask(failed, [failed, completed])).toBe(false)
    expect(isUnresolvedFailedTask(completed, [failed, completed])).toBe(false)
  })

  it('keeps a failed task when it is still the latest attempt', () => {
    const completed = task({ id: 1, status: 'completed', media_item_id: 27 })
    const failed = task({ id: 2, status: 'failed', media_item_id: 27 })
    expect(isUnresolvedFailedTask(failed, [completed, failed])).toBe(true)
  })

  it('prefers the latest task per language', () => {
    const older = task({ id: 1, status: 'failed', target_language_code: 'pt-PT' })
    const newer = task({ id: 2, status: 'completed', target_language_code: 'pt-PT' })
    const map = latestTasksByLanguage([newer, older])
    expect(map.get('pt-PT')?.id).toBe(2)
  })
})

describe('task progress helpers', () => {
  it('prefers the in-flight job', () => {
    const row = task({
      executions: [
        { id: 1, status: 'completed', progress: 100, job_kind: 'extract' } as never,
        { id: 2, status: 'processing', progress: 40, job_kind: 'translate' } as never,
      ],
    })
    expect(latestActiveJob(row)?.id).toBe(2)
    expect(taskProgressPct(row)).toBe(40)
  })
})

describe('task elapsed start', () => {
  it('prefers the in-flight job start', () => {
    const row = task({
      started_at: '2026-08-19 10:00:00',
      created_at: '2026-08-19 09:00:00',
      executions: [
        {
          id: 2,
          status: 'processing',
          progress: 40,
          job_kind: 'translate',
          started_at: '2026-08-19 10:05:00',
          created_at: '2026-08-19 10:04:00',
        } as never,
      ],
    })
    expect(taskElapsedStart(row)).toBe('2026-08-19 10:05:00')
  })
})
