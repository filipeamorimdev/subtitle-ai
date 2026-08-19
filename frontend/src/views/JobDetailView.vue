<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api } from '../services/api'
import type { Job, JobLog, JobUsageExchange } from '../types'
import { formatDateTime } from '../utils/datetime'
import { formatJobLog } from '../utils/formatJobLog'
import { mediaHrefForJob, mediaHrefForTaskId, safeReturnTo, withReturnTo } from '../utils/mediaNav'
import { onLiveEvent } from '../stores/events'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const job = ref<Job | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)
const logBusy = ref(false)
const logVisible = ref(false)
const jobLog = ref<JobLog | null>(null)
const logError = ref<string | null>(null)
const requests = ref<JobUsageExchange[]>([])
const requestsError = ref<string | null>(null)
const requestLogBusy = ref<number | null>(null)
const requestLogModal = ref<{ title: string; body: string; error: string | null } | null>(null)
const mediaHref = ref<string | null>(null)
const retryingId = ref<number | null>(null)
let timer: number | undefined
let lastStatus: string | null = null
let stopLive: (() => void) | undefined

const returnTo = computed(() => safeReturnTo(route.query.from))
const backHref = computed(() => returnTo.value || mediaHref.value || '/media')
const statsHref = computed(() => withReturnTo(`/jobs/${props.id}/stats`, returnTo.value))

const isTranslateJob = computed(() => (job.value?.job_kind || 'translate') === 'translate')
const isDubJob = computed(() => (job.value?.job_kind || '').toLowerCase() === 'dub')
const canShowJobLog = computed(() => isTranslateJob.value || isDubJob.value)
const jobLogTitle = computed(() => (isTranslateJob.value ? 'OpenRouter log' : 'Job log'))

const ACTION_LABELS: Record<string, string> = {
  translate: 'Translate',
  repair: 'Repair',
  glossary_extract: 'Glossary extract',
  glossary_universe: 'Universe classify',
  other: 'Other',
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] || action.replace(/_/g, ' ')
}

function formatTokens(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`
  return String(n)
}

function formatUsd(n: number | null | undefined, digits = 4): string {
  if (n == null) return '—'
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n >= 0.01) return `$${n.toFixed(3)}`
  return `$${n.toFixed(digits)}`
}

function requestTitle(row: JobUsageExchange): string {
  return `#${row.index} ${actionLabel(row.action)} · ${row.model}`
}

const formattedLog = computed(() => formatJobLog(jobLog.value))

const requestsCost = computed(() => {
  const costs = requests.value
    .map((row) => row.cost_usd)
    .filter((n): n is number => n != null)
  if (!costs.length) return null
  return {
    total: costs.reduce((sum, n) => sum + n, 0),
    estimated: requests.value.some((row) => row.cost_usd != null && row.cost_estimated),
  }
})

function notifyFinished(current: Job) {
  if (typeof Notification === 'undefined') return
  if (Notification.permission !== 'granted') return
  const title =
    current.status === 'completed'
      ? `Found ${current.source_language?.toUpperCase() || ''} subtitle`
      : `Subtitle search ${current.status}`
  const body =
    current.status === 'completed'
      ? current.media_title || 'Subtitle ready'
      : current.error || current.progress_detail || current.media_title || 'Finished'
  try {
    new Notification(title.trim(), { body })
  } catch {
    /* ignore */
  }
}

async function loadRequests() {
  try {
    requests.value = await api.getJobRequests(Number(props.id))
    requestsError.value = null
  } catch (err) {
    requests.value = []
    requestsError.value = err instanceof Error ? err.message : String(err)
  }
}

async function load() {
  try {
    job.value = await api.getJob(Number(props.id))
    if (job.value.task_id) {
      try {
        mediaHref.value = await mediaHrefForTaskId(job.value.task_id)
      } catch {
        mediaHref.value = '/media'
      }
    } else {
      try {
        mediaHref.value = await mediaHrefForJob(job.value)
      } catch {
        mediaHref.value = '/media'
      }
    }
    await loadRequests()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function shouldOpenLog() {
  const q = route.query.log
  const value = Array.isArray(q) ? q[0] : q
  return value === '1' || value === 'true' || value === ''
}

onMounted(async () => {
  await load()
  if (shouldOpenLog()) await fetchLog()
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => undefined)
  }
  timer = window.setInterval(() => {
    if (job.value && ['pending', 'processing'].includes(job.value.status)) {
      load()
    }
  }, 15000)
  stopLive = onLiveEvent((event) => {
    if (event.job_id === Number(props.id)) load().catch(() => undefined)
  })
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  stopLive?.()
})

watch(
  () => props.id,
  async () => {
    logVisible.value = false
    jobLog.value = null
    logError.value = null
    requests.value = []
    requestsError.value = null
    requestLogModal.value = null
    await load()
    if (shouldOpenLog()) await fetchLog()
  },
)

watch(
  () => job.value?.status,
  (status) => {
    if (!job.value || !status) return
    if (
      lastStatus &&
      ['pending', 'processing'].includes(lastStatus) &&
      ['completed', 'failed', 'skipped'].includes(status) &&
      job.value.job_kind === 'request'
    ) {
      notifyFinished(job.value)
    }
    lastStatus = status
  },
)

async function retry() {
  await retryAction(Number(props.id))
}

async function retryAction(jobId: number) {
  if (retryingId.value != null || busy.value) return
  busy.value = true
  retryingId.value = jobId
  error.value = null
  try {
    const next = await api.retryJob(jobId)
    await router.push(withReturnTo(`/jobs/${next.id}`, returnTo.value))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
    retryingId.value = null
  }
}

async function cancel() {
  busy.value = true
  error.value = null
  try {
    job.value = await api.cancelJob(Number(props.id))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function retrySync() {
  busy.value = true
  error.value = null
  try {
    job.value = await api.retryBazarrSync(Number(props.id))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function fetchLog() {
  logBusy.value = true
  logError.value = null
  try {
    jobLog.value = await api.getJobLog(Number(props.id))
    logVisible.value = true
  } catch (err) {
    logError.value = err instanceof Error ? err.message : String(err)
    logVisible.value = true
  } finally {
    logBusy.value = false
  }
}

async function toggleLog() {
  if (logVisible.value) {
    logVisible.value = false
    return
  }
  await fetchLog()
}

async function viewRequestLog(row: JobUsageExchange) {
  if (requestLogBusy.value != null) return
  requestLogBusy.value = row.index
  try {
    const log = await api.getJobRequestLog(Number(props.id), row.index)
    if (!log.exists || !log.entry) {
      requestLogModal.value = {
        title: requestTitle(row),
        body: '',
        error: 'No log was recorded for this request.',
      }
      return
    }
    const entry = { ...log.entry }
    if (typeof entry.ts === 'string') {
      entry.ts = formatDateTime(entry.ts)
    }
    requestLogModal.value = {
      title: requestTitle(row),
      body: JSON.stringify(entry, null, 2),
      error: null,
    }
  } catch (err) {
    requestLogModal.value = {
      title: requestTitle(row),
      body: '',
      error: err instanceof Error ? err.message : String(err),
    }
  } finally {
    requestLogBusy.value = null
  }
}
</script>

<template>
  <section v-if="job" class="space-y-6">
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <RouterLink class="text-accent hover:underline" :to="backHref">
        ← Media
      </RouterLink>
      <span class="text-ink-400">/</span>
      <span class="text-ink-500">Job #{{ job.id }}</span>
    </div>
    <p
      v-if="job.task_id && mediaHref"
      class="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-sm"
    >
      This is one execution of a localization task.
      <RouterLink class="font-semibold text-accent hover:underline" :to="mediaHref">
        Open the media page
      </RouterLink>
      for the full progress.
    </p>

    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <h1 class="break-words font-display text-2xl font-bold sm:text-3xl">
          {{ job.media_title || 'Job' }} #{{ job.id }}
        </h1>
        <p class="mt-1 capitalize text-sm text-ink-600 sm:text-base dark:text-ink-300">
          {{ job.status }} · {{ job.progress_detail || `${job.progress}%` }}
        </p>
      </div>
      <div class="flex w-full flex-wrap gap-2 sm:w-auto">
        <button
          v-if="['failed', 'cancelled', 'skipped'].includes(job.status)"
          class="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white"
          type="button"
          :disabled="busy"
          @click="retry"
        >
          Retry
        </button>
        <button
          v-if="['pending', 'processing', 'paused'].includes(job.status)"
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          type="button"
          :disabled="busy"
          @click="cancel"
        >
          Cancel
        </button>
        <button
          v-if="job.status === 'completed' && job.warning"
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          type="button"
          :disabled="busy"
          @click="retrySync"
        >
          Retry Bazarr sync
        </button>
        <button
          v-if="canShowJobLog"
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          type="button"
          :disabled="logBusy"
          @click="toggleLog"
        >
          {{ logVisible ? 'Hide log' : logBusy ? 'Loading log…' : 'View log' }}
        </button>
        <RouterLink
          v-if="isTranslateJob"
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          :to="statsHref"
        >
          Usage stats
        </RouterLink>
      </div>
    </div>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>

    <div
      v-if="logVisible"
      class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
    >
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="font-display text-lg font-bold">{{ jobLogTitle }}</h2>
          <p v-if="jobLog" class="mt-1 break-all text-sm text-ink-500">{{ jobLog.path }}</p>
        </div>
        <p v-if="jobLog?.exists" class="text-sm text-ink-500">{{ jobLog.entry_count }} entries</p>
      </div>
      <p v-if="logError" class="mt-3 text-sm text-red-700 dark:text-red-300">{{ logError }}</p>
      <p
        v-else-if="jobLog && !jobLog.exists"
        class="mt-3 text-sm text-ink-600 dark:text-ink-300"
      >
        {{ isTranslateJob ? 'No OpenRouter log file for this job yet. Logs are written when translation API calls run.' : 'No job event log file for this job yet.' }}
      </p>
      <pre
        v-else-if="formattedLog"
        class="mt-4 max-h-[32rem] overflow-auto rounded-lg bg-ink-950 p-4 text-xs leading-relaxed text-ink-100"
      >{{ formattedLog }}</pre>
    </div>

    <dl class="grid gap-4 rounded-xl border border-ink-200 bg-white/80 p-5 text-sm dark:border-ink-800 dark:bg-ink-900/60 sm:grid-cols-3">
      <div>
        <dt class="text-ink-500">Created</dt>
        <dd class="mt-1">{{ formatDateTime(job.created_at) }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Started</dt>
        <dd class="mt-1">{{ formatDateTime(job.started_at) }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Completed</dt>
        <dd class="mt-1">{{ formatDateTime(job.completed_at) }}</dd>
      </div>
      <div class="sm:col-span-3" v-if="job.error">
        <dt class="text-ink-500">Error</dt>
        <dd class="mt-1 text-red-700 dark:text-red-300">{{ job.error }}</dd>
      </div>
      <div class="sm:col-span-3" v-if="job.warning">
        <dt class="text-ink-500">Warning</dt>
        <dd class="mt-1 text-amber-700 dark:text-amber-300">{{ job.warning }}</dd>
      </div>
    </dl>

    <div
      v-if="isTranslateJob"
      class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
    >
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="font-display text-lg font-bold">Requests</h2>
          <p class="mt-1 text-sm text-ink-500">API calls made for this job</p>
        </div>
        <div class="text-right text-sm text-ink-500">
          <p>{{ requests.length }} total</p>
          <p v-if="job.total_tokens != null">
            <RouterLink class="text-accent hover:underline" :to="statsHref">
              {{ job.total_tokens }} tokens
              <span v-if="job.input_tokens != null">
                (in {{ job.input_tokens }} / out {{ job.output_tokens }})
              </span>
            </RouterLink>
          </p>
          <p v-if="requestsCost">
            {{ formatUsd(requestsCost.total) }}
            <span v-if="requestsCost.estimated">est.</span>
          </p>
        </div>
      </div>

      <p v-if="requestsError" class="mt-4 text-sm text-red-700 dark:text-red-300">
        {{ requestsError }}
      </p>
      <p v-else-if="!requests.length" class="mt-4 text-sm text-ink-600 dark:text-ink-300">
        No API requests recorded for this job yet.
      </p>
      <div v-else class="mt-4 overflow-x-auto">
        <table class="min-w-full text-left text-sm">
          <thead class="border-b border-ink-200 text-ink-500 dark:border-ink-800 dark:text-ink-300">
            <tr>
              <th class="py-2 pr-4 font-medium">#</th>
              <th class="py-2 pr-4 font-medium">Time</th>
              <th class="py-2 pr-4 font-medium">Action</th>
              <th class="py-2 pr-4 font-medium">Model</th>
              <th class="py-2 pr-4 font-medium">Tokens</th>
              <th class="py-2 pr-4 font-medium">Cost</th>
              <th class="py-2 pr-4 font-medium">Status</th>
              <th class="py-2 font-medium">Log</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in requests"
              :key="row.index"
              class="border-b border-ink-100 last:border-0 dark:border-ink-800/80"
            >
              <td class="py-3 pr-4 align-top text-ink-500">{{ row.index }}</td>
              <td class="py-3 pr-4 align-top whitespace-nowrap text-ink-600 dark:text-ink-300">
                {{ formatDateTime(row.ts) }}
              </td>
              <td class="py-3 pr-4 align-top capitalize">
                {{ actionLabel(row.action) }}
                <span v-if="row.attempt && row.attempt > 1" class="text-xs text-ink-500">
                  · attempt {{ row.attempt }}
                </span>
              </td>
              <td class="py-3 pr-4 align-top truncate max-w-[14rem]" :title="row.model">
                {{ row.model }}
              </td>
              <td class="py-3 pr-4 align-top whitespace-nowrap">
                {{ formatTokens(row.total_tokens) }}
                <span class="text-ink-500">
                  ({{ formatTokens(row.input_tokens) }}/{{ formatTokens(row.output_tokens) }})
                </span>
              </td>
              <td class="py-3 pr-4 align-top whitespace-nowrap">
                {{ formatUsd(row.cost_usd) }}
                <span v-if="row.cost_estimated" class="text-xs text-ink-500">est.</span>
              </td>
              <td class="py-3 pr-4 align-top">
                <span v-if="row.ok" class="text-ink-600 dark:text-ink-300">
                  {{ row.status_code || 'ok' }}
                </span>
                <span v-else class="text-red-700 dark:text-red-300">
                  {{ row.error || row.status_code || 'failed' }}
                </span>
              </td>
              <td class="py-3 align-top">
                <button
                  type="button"
                  class="rounded-md border border-ink-300 px-2 py-1 text-xs font-semibold dark:border-ink-600"
                  :disabled="requestLogBusy != null"
                  @click="viewRequestLog(row)"
                >
                  {{ requestLogBusy === row.index ? 'Loading…' : 'View log' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div
      v-if="requestLogModal"
      class="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/50 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="request-log-title"
      @click.self="requestLogModal = null"
    >
      <div
        class="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-ink-200 bg-white p-5 shadow-xl dark:border-ink-700 dark:bg-ink-900"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 id="request-log-title" class="break-words font-display text-lg font-bold">
              {{ requestLogModal.title }}
            </h2>
            <p class="mt-1 text-sm text-ink-500">Request and response for this API call</p>
          </div>
          <button
            type="button"
            class="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
            @click="requestLogModal = null"
          >
            Close
          </button>
        </div>
        <p v-if="requestLogModal.error" class="mt-4 text-sm text-red-700 dark:text-red-300">
          {{ requestLogModal.error }}
        </p>
        <pre
          v-else
          class="mt-4 min-h-0 flex-1 overflow-auto rounded-lg bg-ink-950 p-4 text-xs leading-relaxed text-ink-100"
        >{{ requestLogModal.body }}</pre>
      </div>
    </div>
  </section>
  <p v-else class="text-ink-500">Loading job…</p>
</template>
