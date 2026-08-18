import type { JobLog } from '../types'
import { formatDateTime } from './datetime'

function pretty(value: unknown): string {
  return JSON.stringify(value ?? null, null, 2)
}

/** Render a job OpenRouter log with numbered request/response exchanges. */
export function formatJobLog(jobLog: JobLog | null | undefined): string {
  if (!jobLog?.exists) return ''
  const entries = jobLog.entries
  if (!entries?.length) return jobLog.content || ''

  const parts: string[] = []
  let exchangeIndex = 0

  for (const entry of entries) {
    const ts = typeof entry.ts === 'string' ? formatDateTime(entry.ts) : ''

    if (entry.event === 'exchange') {
      exchangeIndex += 1
      const headerBits: string[] = []
      if (ts) headerBits.push(ts)
      if (typeof entry.error === 'string' && entry.error) {
        headerBits.push(`error=${entry.error}`)
      }
      const block = [
        `========== exchange #${exchangeIndex} ==========`,
        ...headerBits,
        '',
        '----- request -----',
        pretty(entry.request),
        '',
        '----- response -----',
        pretty(entry.response),
      ]
      parts.push(block.join('\n'))
      continue
    }

    const rest = { ...entry }
    delete rest.ts
    const label = typeof entry.event === 'string' && entry.event ? entry.event : 'event'
    parts.push([`${label}${ts ? `  ${ts}` : ''}`, pretty(rest)].join('\n'))
  }

  return parts.join('\n\n')
}
