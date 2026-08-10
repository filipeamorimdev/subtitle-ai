<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { api } from '../services/api'
import type { Job, JobAction, JobLog } from '../types'
import { formatDateTime, formatDuration } from '../utils/datetime'

const props = defineProps<{ id: string }>()
const router = useRouter()
const job = ref<Job | null>(null)
const actions = ref<JobAction[]>([])
const error = ref<string | null>(null)
const actionsError = ref<string | null>(null)
const busy = ref(false)
const logBusy = ref(false)
const logVisible = ref(false)
const jobLog = ref<JobLog | null>(null)
const logError = ref<string | null>(null)
let timer: number | undefined
let lastStatus: string | null = null

const isTranslateJob = computed(() => (job.value?.job_kind || 'translate') === 'translate')

const formattedLog = computed(() => {
  if (!jobLog.value?.exists) return ''
  if (jobLog.value.entries?.length) {
    return jobLog.value.entries
      .map((entry) => {
        const normalized = { ...entry }
        if (typeof normalized.ts === 'string') {
          normalized.ts = formatDateTime(normalized.ts)
        }
        return JSON.stringify(normalized, null, 2)
      })
      .join('\n\n')
  }
  return jobLog.value.content || ''
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

function statusClass(status: string) {
  if (status === 'completed') return 'text-emerald-700 dark:text-emerald-300'
  if (status === 'failed') return 'text-red-700 dark:text-red-300'
  if (status === 'cancelled' || status === 'skipped') return 'text-amber-700 dark:text-amber-300'
  if (status === 'processing') return 'text-accent'
  return 'text-ink-700 dark:text-ink-200'
}

async function loadActions() {
  try {
    actions.value = await api.getJobActions(Number(props.id))
    actionsError.value = null
  } catch (err) {
    actionsError.value = err instanceof Error ? err.message : String(err)
  }
}

async function load() {
  try {
    job.value = await api.getJob(Number(props.id))
    await loadActions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(async () => {
  await load()
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => undefined)
  }
  timer = window.setInterval(() => {
    if (job.value && ['pending', 'processing'].includes(job.value.status)) {
      load()
    }
  }, 2000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})

watch(
  () => props.id,
  () => {
    logVisible.value = false
    jobLog.value = null
    logError.value = null
    actions.value = []
    actionsError.value = null
    load()
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
  busy.value = true
  error.value = null
  try {
    const next = await api.retryJob(Number(props.id))
    await router.push(`/jobs/${next.id}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function cancel() {
  busy.value = true
  error.value = null
  try {
    job.value = await api.cancelJob(Number(props.id))
    await loadActions()
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
    await loadActions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function toggleLog() {
  if (logVisible.value) {
    logVisible.value = false
    return
  }
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
</script>

<template>
  <section v-if="job" class="space-y-6">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <h1 class="break-words font-display text-2xl font-bold sm:text-3xl">
          {{ job.media_title || 'Job' }} #{{ job.id }}
        </h1>
        <p class="mt-1 capitalize text-sm text-ink-600 sm:text-base dark:text-ink-300">
          {{ job.job_kind || 'translate' }} · {{ job.status }} · {{ job.progress }}%
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
          v-if="['pending', 'processing'].includes(job.status)"
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
          v-if="isTranslateJob"
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          type="button"
          :disabled="logBusy"
          @click="toggleLog"
        >
          {{ logVisible ? 'Hide log' : logBusy ? 'Loading log…' : 'View log' }}
        </button>
        <RouterLink
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          :to="`/jobs/${job.id}/stats`"
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
          <h2 class="font-display text-lg font-bold">OpenRouter log</h2>
          <p v-if="jobLog" class="mt-1 break-all text-sm text-ink-500">{{ jobLog.path }}</p>
        </div>
        <p v-if="jobLog?.exists" class="text-sm text-ink-500">{{ jobLog.entry_count }} entries</p>
      </div>
      <p v-if="logError" class="mt-3 text-sm text-red-700 dark:text-red-300">{{ logError }}</p>
      <p
        v-else-if="jobLog && !jobLog.exists"
        class="mt-3 text-sm text-ink-600 dark:text-ink-300"
      >
        No OpenRouter log file for this job yet. Logs are written when translation API calls run.
      </p>
      <pre
        v-else-if="formattedLog"
        class="mt-4 max-h-[32rem] overflow-auto rounded-lg bg-ink-950 p-4 text-xs leading-relaxed text-ink-100"
      >{{ formattedLog }}</pre>
    </div>

    <div class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="font-display text-lg font-bold">Actions</h2>
          <p class="mt-1 text-sm text-ink-500">
            Every request, extract, and translate run for this
            {{ job.media_type === 'episode' ? 'episode' : 'media item' }}.
          </p>
        </div>
        <p class="text-sm text-ink-500">{{ actions.length }} total</p>
      </div>

      <p v-if="actionsError" class="mt-3 text-sm text-red-700 dark:text-red-300">{{ actionsError }}</p>

      <div v-else class="mt-4 overflow-x-auto">
        <table class="min-w-full text-left text-sm">
          <thead class="border-b border-ink-200 text-ink-500 dark:border-ink-800 dark:text-ink-300">
            <tr>
              <th class="py-2 pr-4 font-medium">Action</th>
              <th class="py-2 pr-4 font-medium">Date / time</th>
              <th class="py-2 pr-4 font-medium">Duration</th>
              <th class="py-2 pr-4 font-medium">Status</th>
              <th class="py-2 font-medium">Message</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!actions.length">
              <td colspan="5" class="py-4 text-ink-500">No actions recorded yet.</td>
            </tr>
            <tr
              v-for="item in actions"
              :key="item.id"
              class="border-b border-ink-100 last:border-0 dark:border-ink-800/80"
              :class="item.current ? 'bg-accent/5' : ''"
            >
              <td class="py-3 pr-4 align-top">
                <RouterLink
                  v-if="!item.current"
                  class="capitalize text-accent hover:underline"
                  :to="`/jobs/${item.id}`"
                >
                  {{ item.action }}
                  <span class="text-ink-500">#{{ item.id }}</span>
                </RouterLink>
                <span v-else class="capitalize font-medium">
                  {{ item.action }}
                  <span class="text-ink-500">#{{ item.id }}</span>
                </span>
              </td>
              <td class="py-3 pr-4 align-top whitespace-nowrap text-ink-600 dark:text-ink-300">
                {{ formatDateTime(item.datetime) }}
              </td>
              <td class="py-3 pr-4 align-top whitespace-nowrap text-ink-600 dark:text-ink-300">
                {{ formatDuration(item.duration_seconds) }}
              </td>
              <td class="py-3 pr-4 align-top capitalize" :class="statusClass(item.status)">
                {{ item.status }}
              </td>
              <td
                class="py-3 align-top break-words"
                :class="
                  item.status === 'failed'
                    ? 'text-red-700 dark:text-red-300'
                    : 'text-ink-600 dark:text-ink-300'
                "
              >
                {{ item.message || '—' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <dl class="grid gap-4 rounded-xl border border-ink-200 bg-white/80 p-5 text-sm dark:border-ink-800 dark:bg-ink-900/60 sm:grid-cols-2">
      <div>
        <dt class="text-ink-500">Media</dt>
        <dd class="mt-1 break-all">{{ job.media_path }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Kind</dt>
        <dd class="mt-1 capitalize">{{ job.job_kind || 'translate' }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Type</dt>
        <dd class="mt-1 capitalize">{{ job.media_type }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Source subtitle</dt>
        <dd class="mt-1 break-all">{{ job.source_subtitle_path }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Target subtitle</dt>
        <dd class="mt-1 break-all">{{ job.target_subtitle_path }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Languages</dt>
        <dd class="mt-1">{{ job.source_language }} → {{ job.target_language }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Model</dt>
        <dd class="mt-1">{{ job.model }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Progress</dt>
        <dd class="mt-1">{{ job.progress_detail || `${job.progress}%` }}</dd>
      </div>
      <div>
        <dt class="text-ink-500">Tokens</dt>
        <dd class="mt-1">
          <RouterLink class="text-accent hover:underline" :to="`/jobs/${job.id}/stats`">
            {{ job.total_tokens ?? '—' }}
            <span v-if="job.input_tokens != null" class="text-ink-500">
              (in {{ job.input_tokens }} / out {{ job.output_tokens }})
            </span>
          </RouterLink>
        </dd>
      </div>
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
      <div>
        <dt class="text-ink-500">Reason</dt>
        <dd class="mt-1">{{ job.reason_code || '—' }}</dd>
      </div>
      <div class="sm:col-span-2" v-if="job.error">
        <dt class="text-ink-500">Error</dt>
        <dd class="mt-1 text-red-700 dark:text-red-300">{{ job.error }}</dd>
      </div>
      <div class="sm:col-span-2" v-if="job.warning">
        <dt class="text-ink-500">Warning</dt>
        <dd class="mt-1 text-amber-700 dark:text-amber-300">{{ job.warning }}</dd>
      </div>
    </dl>
  </section>
  <p v-else class="text-ink-500">Loading job…</p>
</template>
