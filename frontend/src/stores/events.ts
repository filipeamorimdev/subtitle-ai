export type LiveEvent = {
  type: string
  job_id?: number | null
  task_id?: number | null
  status?: string | null
  progress?: number | null
  detail?: string | null
  media_item_id?: number | null
  ts?: string
}

type Handler = (event: LiveEvent) => void

const handlers = new Set<Handler>()
let source: EventSource | null = null
let reconnect: number | undefined

export function onLiveEvent(handler: Handler) {
  handlers.add(handler)
  return () => {
    handlers.delete(handler)
  }
}

export function startLiveEvents() {
  if (typeof window === 'undefined' || source) return
  connect()
}

function connect() {
  try {
    source = new EventSource('/api/events')
  } catch {
    scheduleReconnect()
    return
  }
  source.onmessage = (message) => {
    try {
      const data = JSON.parse(message.data) as LiveEvent
      handlers.forEach((handler) => handler(data))
    } catch {
      /* ignore malformed payloads */
    }
  }
  source.onerror = () => {
    source?.close()
    source = null
    scheduleReconnect()
  }
}

function scheduleReconnect() {
  if (reconnect) return
  reconnect = window.setTimeout(() => {
    reconnect = undefined
    connect()
  }, 4000)
}
