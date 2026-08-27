<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import { onLiveEvent } from '../stores/events'
import type { OperatorMessage, OperatorTurn } from '../types'
import { mediaHref } from '../utils/mediaNav'

const store = useAppStore()

const expanded = ref(false)
const input = ref('')
const sending = ref(false)
const error = ref<string | null>(null)
const sessionId = ref<number | null>(null)
const messages = ref<OperatorMessage[]>([])
const toolEvents = ref<OperatorTurn['tool_events']>([])
const pending = ref<OperatorTurn['pending_confirmation']>(null)
const mediaLinks = ref<OperatorTurn['media_links']>([])
const recap = ref<string | null>(null)
const ready = ref(false)
const disabledReason = ref<string | null>(null)
const rootEl = ref<HTMLElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLInputElement | null>(null)

const speechSupported = computed(
  () => typeof window !== 'undefined' && !!(window as any).webkitSpeechRecognition,
)
const listening = ref(false)
let recognition: any = null
let stopLive: (() => void) | undefined

const canSend = computed(
  () => ready.value && !sending.value && Boolean(input.value.trim() || pending.value),
)

async function ensureSession() {
  if (sessionId.value != null) return sessionId.value
  const cached = sessionStorage.getItem('operator_session_id')
  if (cached) {
    const id = Number(cached)
    if (Number.isFinite(id) && id > 0) {
      try {
        const detail = await api.getOperatorSession(id)
        sessionId.value = detail.id
        messages.value = detail.messages.filter((m) => m.role !== 'system')
        ready.value = detail.operator_ready
        return detail.id
      } catch {
        sessionStorage.removeItem('operator_session_id')
      }
    }
  }
  const created = await api.createOperatorSession()
  sessionId.value = created.id
  sessionStorage.setItem('operator_session_id', String(created.id))
  return created.id
}

async function refreshStatus() {
  try {
    const status = await api.getOperatorStatus()
    ready.value = status.ready
    disabledReason.value = status.ready
      ? null
      : status.reason || 'Configure AI in Settings'
  } catch {
    ready.value = false
    disabledReason.value = 'Operator chat unavailable'
  }
}

function collapseIfIdle() {
  if (sending.value) return
  expanded.value = false
}

function expand() {
  if (!ready.value) return
  expanded.value = true
  nextTick(() => inputEl.value?.focus())
}

function onDocumentPointer(event: MouseEvent) {
  if (!expanded.value) return
  const target = event.target as Node | null
  if (rootEl.value && target && !rootEl.value.contains(target)) {
    collapseIfIdle()
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && expanded.value) {
    collapseIfIdle()
  }
}

function toolLabel(name: string): string {
  const labels: Record<string, string> = {
    search_media: 'Searched media',
    ensure_media: 'Saved media',
    normalize_language: 'Normalized language',
    get_media_localization: 'Checked localization',
    create_localization_task: 'Started task',
    transcribe_audio: 'Transcribe',
    start_dub: 'Dub',
    list_tasks: 'Listed tasks',
    get_task: 'Task status',
    retry_task: 'Retry task',
    cancel_task: 'Cancel task',
  }
  return labels[name] || name
}

function applyTurn(turn: OperatorTurn) {
  toolEvents.value = [...toolEvents.value, ...turn.tool_events]
  pending.value = turn.pending_confirmation
  if (turn.media_links?.length) {
    const seen = new Set(mediaLinks.value.map((m) => m.media_id))
    for (const link of turn.media_links) {
      if (!seen.has(link.media_id)) {
        mediaLinks.value.push(link)
        seen.add(link.media_id)
      }
    }
  }
  if (turn.assistant_text) {
    messages.value.push({
      id: Date.now(),
      role: 'assistant',
      content: turn.assistant_text,
    })
  }
  const started = turn.tool_events.find(
    (e) => e.tool === 'create_localization_task' && e.result && (e.result as any).ok,
  )
  if (started?.result) {
    const r = started.result as Record<string, any>
    const title = r.media?.title || 'Media'
    const lang = r.target_language_name || r.target_language_code || ''
    recap.value = `${title}${lang ? ` → ${lang}` : ''} · ${r.status || 'planning'}`
  } else if (turn.assistant_text) {
    recap.value = turn.assistant_text.slice(0, 80)
  }
}

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || sending.value || !ready.value) return
  sending.value = true
  error.value = null
  expanded.value = true
  try {
    const id = await ensureSession()
    messages.value.push({ id: Date.now(), role: 'user', content })
    input.value = ''
    const turn = await api.postOperatorMessage(id, { content })
    applyTurn(turn)
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    sending.value = false
  }
}

async function confirmPending(ok: boolean) {
  if (!pending.value?.tool || !sessionId.value) return
  if (!ok) {
    pending.value = null
    messages.value.push({
      id: Date.now(),
      role: 'assistant',
      content: 'Okay, cancelled.',
    })
    return
  }
  sending.value = true
  error.value = null
  try {
    const turn = await api.postOperatorMessage(sessionId.value, {
      content: '',
      confirmed_tool: {
        name: pending.value.tool,
        arguments: pending.value.arguments || {},
      },
    })
    pending.value = null
    applyTurn(turn)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    sending.value = false
  }
}

function pickSearchHit(hit: Record<string, unknown>) {
  const title = String(hit.title || '')
  const year = hit.year ? ` (${hit.year})` : ''
  void send(`Use ${title}${year}`)
}

function searchHitsFromEvents(): Record<string, unknown>[] {
  for (let i = toolEvents.value.length - 1; i >= 0; i -= 1) {
    const ev = toolEvents.value[i]
    if (ev.tool !== 'search_media') continue
    const result = ev.result as Record<string, unknown> | undefined
    if (result?.ambiguous && Array.isArray(result.results)) {
      return result.results as Record<string, unknown>[]
    }
  }
  return []
}

const ambiguousHits = computed(() => searchHitsFromEvents())

function toggleMic() {
  if (!speechSupported.value) return
  const SR = (window as any).webkitSpeechRecognition
  if (!SR) return
  if (listening.value && recognition) {
    recognition.stop()
    listening.value = false
    return
  }
  recognition = new SR()
  recognition.lang = 'en-US'
  recognition.interimResults = false
  recognition.onresult = (event: any) => {
    const transcript = event.results?.[0]?.[0]?.transcript
    if (typeof transcript === 'string' && transcript.trim()) {
      input.value = transcript.trim()
      expand()
    }
  }
  recognition.onerror = () => {
    listening.value = false
  }
  recognition.onend = () => {
    listening.value = false
  }
  listening.value = true
  recognition.start()
}

onMounted(async () => {
  await store.loadSettings().catch(() => undefined)
  await refreshStatus()
  document.addEventListener('mousedown', onDocumentPointer)
  document.addEventListener('keydown', onKeydown)
  stopLive = onLiveEvent((event) => {
    if (event.type === 'hello') return
    if (!recap.value) return
    // Soft nudge: keep recap text; dashboard already reloads tasks.
  })
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onDocumentPointer)
  document.removeEventListener('keydown', onKeydown)
  stopLive?.()
  try {
    recognition?.stop?.()
  } catch {
    /* ignore */
  }
})

watch(
  () => store.settings?.openrouter_api_key_configured,
  () => {
    void refreshStatus()
  },
)
</script>

<template>
  <div ref="rootEl" class="w-full">
    <!-- Collapsed bar -->
    <button
      v-if="!expanded"
      type="button"
      class="flex w-full items-center gap-2 rounded-2xl border border-ink-200 bg-white/50 px-4 py-2.5 text-left text-sm text-ink-500 shadow-sm transition hover:border-ink-300 hover:bg-white dark:border-ink-700 dark:bg-ink-900/40 dark:hover:border-ink-600 dark:hover:bg-ink-900/70"
      :class="{ 'cursor-not-allowed opacity-60': !ready }"
      :disabled="!ready && !disabledReason"
      @click="ready ? expand() : undefined"
    >
      <span class="min-w-0 flex-1 truncate">
        <template v-if="!ready">
          {{ disabledReason || 'Configure AI in Settings' }}
          <RouterLink
            v-if="!ready"
            class="ml-1 font-medium text-accent hover:underline"
            to="/settings/models"
            @click.stop
          >
            Open settings
          </RouterLink>
        </template>
        <template v-else-if="recap">{{ recap }}</template>
        <template v-else>Ask Subtitle AI…</template>
      </span>
      <span
        v-if="speechSupported && ready"
        class="shrink-0 text-ink-400"
        aria-hidden="true"
      >🎤</span>
      <span class="shrink-0 text-ink-400" aria-hidden="true">➤</span>
    </button>

    <!-- Expanded panel -->
    <div
      v-else
      class="overflow-hidden rounded-2xl border border-ink-200 bg-white/90 shadow-md dark:border-ink-700 dark:bg-ink-900/80"
    >
      <div class="flex items-center justify-between border-b border-ink-100 px-4 py-2 dark:border-ink-800">
        <div class="text-xs font-semibold uppercase tracking-wide text-ink-500">Ask Subtitle AI</div>
        <button
          type="button"
          class="text-xs text-ink-500 hover:text-ink-800 dark:hover:text-ink-200"
          @click="collapseIfIdle"
        >
          Collapse
        </button>
      </div>

      <div ref="listEl" class="max-h-[420px] space-y-3 overflow-y-auto px-4 py-3">
        <p v-if="!messages.length" class="text-sm text-ink-500">
          Try “Portuguese subs for The Matrix” — actions run through tools, not guesses.
        </p>
        <div
          v-for="(msg, idx) in messages.filter((m) => m.role === 'user' || m.role === 'assistant')"
          :key="`${msg.id}-${idx}`"
          class="text-sm"
          :class="msg.role === 'user' ? 'text-ink-800 dark:text-ink-100' : 'text-ink-600 dark:text-ink-300'"
        >
          <span class="mr-2 text-[10px] font-semibold uppercase tracking-wide text-ink-400">
            {{ msg.role === 'user' ? 'You' : 'AI' }}
          </span>
          {{ msg.content }}
        </div>

        <div v-if="toolEvents.length" class="flex flex-wrap gap-2">
          <template v-for="(ev, i) in toolEvents" :key="`tool-${i}`">
            <RouterLink
              v-if="(ev.result as any)?.media_id || (ev.result as any)?.media?.media_id"
              class="inline-flex items-center rounded-full border border-sky-200 bg-sky-50 px-2.5 py-0.5 text-xs font-medium text-sky-800 hover:underline dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-200"
              :to="mediaHref(Number((ev.result as any).media_id || (ev.result as any).media?.media_id))"
            >
              {{ toolLabel(ev.tool) }}
              <template v-if="(ev.result as any)?.task_id">
                · #{{ (ev.result as any).task_id }}
              </template>
            </RouterLink>
            <span
              v-else
              class="inline-flex items-center rounded-full border border-ink-200 bg-ink-50 px-2.5 py-0.5 text-xs text-ink-600 dark:border-ink-700 dark:bg-ink-950/40 dark:text-ink-300"
            >
              {{ toolLabel(ev.tool) }}
            </span>
          </template>
        </div>

        <div v-if="ambiguousHits.length" class="space-y-1">
          <p class="text-xs text-ink-500">Pick a title:</p>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="(hit, i) in ambiguousHits"
              :key="`hit-${i}`"
              type="button"
              class="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-900 hover:bg-violet-100 dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-100"
              @click="pickSearchHit(hit)"
            >
              {{ hit.title }}<template v-if="hit.year"> ({{ hit.year }})</template>
            </button>
          </div>
        </div>

        <div
          v-if="pending"
          class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm dark:border-amber-800 dark:bg-amber-950/30"
        >
          <p class="font-medium text-amber-900 dark:text-amber-100">
            {{ pending.preview || `Confirm ${pending.tool}?` }}
          </p>
          <div class="mt-2 flex gap-2">
            <button
              type="button"
              class="rounded-md bg-accent px-3 py-1 text-xs font-semibold text-white"
              :disabled="sending"
              @click="confirmPending(true)"
            >
              Confirm
            </button>
            <button
              type="button"
              class="rounded-md border border-ink-300 px-3 py-1 text-xs font-semibold dark:border-ink-600"
              :disabled="sending"
              @click="confirmPending(false)"
            >
              Don't
            </button>
          </div>
        </div>

        <p v-if="error" class="text-sm text-red-700 dark:text-red-300">{{ error }}</p>
        <p v-if="sending" class="text-xs text-ink-500">Thinking…</p>
      </div>

      <form
        class="flex items-center gap-2 border-t border-ink-100 px-3 py-2 dark:border-ink-800"
        @submit.prevent="send()"
      >
        <input
          ref="inputEl"
          v-model="input"
          type="text"
          class="min-w-0 flex-1 rounded-xl border-0 bg-transparent px-2 py-2 text-sm outline-none placeholder:text-ink-400"
          placeholder="Ask Subtitle AI…"
          :disabled="sending || !ready"
          autocomplete="off"
        />
        <button
          v-if="speechSupported"
          type="button"
          class="rounded-md px-2 py-1.5 text-sm"
          :class="listening ? 'text-accent' : 'text-ink-500 hover:text-ink-800 dark:hover:text-ink-200'"
          :disabled="!ready"
          title="Voice input"
          @click="toggleMic"
        >
          🎤
        </button>
        <button
          type="submit"
          class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
          :disabled="!canSend"
        >
          Send
        </button>
      </form>
    </div>
  </div>
</template>
