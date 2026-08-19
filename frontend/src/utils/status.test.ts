import { describe, expect, it } from 'vitest'
import { canRetryTask, isActiveTaskStatus, taskStatusLabel } from './status'
import { latestActiveJob, taskProgressPct } from './taskProgress'
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
