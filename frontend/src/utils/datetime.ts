/** Format timestamps as Y-m-d H:i:s (YYYY-MM-DD HH:MM:SS, UTC). */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)) return value

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
  )
}

/** Parse API timestamps (`YYYY-MM-DD HH:MM:SS` UTC, or ISO) into a Date. */
export function parseDateTime(value: string | null | undefined): Date | null {
  if (!value) return null
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)) {
    const date = new Date(`${value.replace(' ', 'T')}Z`)
    return Number.isNaN(date.getTime()) ? null : date
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Format a duration in seconds as a compact human-readable string. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`
  if (minutes > 0) return `${minutes}m ${secs}s`
  if (total < 1 && seconds > 0) return `${seconds.toFixed(1)}s`
  return `${secs}s`
}

/** Format elapsed time since start, e.g. `45s`, `20 min`, `1h 5 min`. */
export function formatElapsed(start: string | null | undefined, nowMs: number = Date.now()): string {
  const date = parseDateTime(start)
  if (!date) return '0s'
  const seconds = Math.max(0, Math.floor((nowMs - date.getTime()) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) return `${hours}h ${minutes} min`
  if (minutes > 0) return `${minutes} min`
  return `${secs}s`
}
