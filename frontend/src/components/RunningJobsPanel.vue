<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { api } from '../services/api'
import type { Job, JobLog, LocalizationTask } from '../types'
import { formatElapsed } from '../utils/datetime'
import { formatJobLog } from '../utils/formatJobLog'
import { localizationTaskTitle, mediaHref, withReturnTo } from '../utils/mediaNav'
import {
  canPauseJob,
  canResumeJob,
  jobHasAiArtifacts,
  jobKindLabel,
  jobStatusClass,
  taskStatusLabel,
} from '../utils/status'
import { latestActiveJob } from '../utils/taskProgress'

const props = withDefaults(
  defineProps<{
    tasks: LocalizationTask[]
    busy?: boolean
    showTitle?: boolean
  }>(),
  {
    busy: false,
    showTitle: false,
  },
)

const emit = defineEmits<{
  cancel: [taskId: number]
  refreshed: []
}>()

const route = useRoute()

interface RunningRow {
  task: LocalizationTask
  job: Job | null
}

const now = ref(Date.now())
const logByJob = ref<Record<number, JobLog | null>>({})
const logErrorByJob = ref<Record<number, string | null>>({})
const logOpen = ref<Set<number>>(new Set())
const logBusyId = ref<number | null>(null)
const jobActionId = ref<number | null>(null)
let tick: number | undefined

const iconBtnClass =
  'inline-flex shrink-0 items-center justify-center rounded-md p-1.5 text-ink-500 transition hover:bg-ink-100 hover:text-accent disabled:opacity-50 dark:hover:bg-ink-800'

onMounted(() => {
  tick = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (tick) window.clearInterval(tick)
})

const rows = computed<RunningRow[]>(() =>
  props.tasks.map((task) => ({ task, job: latestActiveJob(task) })),
)

function progressSummary(row: RunningRow) {
  const start =
    row.job?.started_at || row.task.started_at || row.job?.created_at || row.task.created_at
  const elapsed = formatElapsed(start, now.value)
  const live =
    row.job?.status === 'pending' || row.job?.status === 'processing'
      ? row.job?.progress
      : row.task.status === 'verifying'
        ? 100
        : 0
  const pct = Math.round(Math.min(100, Math.max(0, live ?? 0)))
  return `${elapsed} - ${pct}%`
}

function stepClass(state: string) {
  if (state === 'done') return 'text-emerald-700 dark:text-emerald-300'
  if (state === 'active') return 'font-semibold text-accent'
  if (state === 'failed') return 'text-red-700 dark:text-red-300'
  if (state === 'skipped') return 'text-ink-400 line-through'
  return 'text-ink-400'
}

function isDubTask(task: LocalizationTask) {
  return (task.capability || 'subtitles').toLowerCase() === 'audio'
}

function capabilityLabel(task: LocalizationTask) {
  return isDubTask(task) ? 'Audio dub' : 'Subtitles'
}

async function toggleLog(jobId: number) {
  if (logOpen.value.has(jobId)) {
    const next = new Set(logOpen.value)
    next.delete(jobId)
    logOpen.value = next
    return
  }
  logBusyId.value = jobId
  try {
    logByJob.value = { ...logByJob.value, [jobId]: await api.getJobLog(jobId) }
    logErrorByJob.value = { ...logErrorByJob.value, [jobId]: null }
    logOpen.value = new Set(logOpen.value).add(jobId)
  } catch (err) {
    logErrorByJob.value = {
      ...logErrorByJob.value,
      [jobId]: err instanceof Error ? err.message : String(err),
    }
    logOpen.value = new Set(logOpen.value).add(jobId)
  } finally {
    logBusyId.value = null
  }
}

async function pauseJob(jobId: number) {
  if (jobActionId.value != null) return
  jobActionId.value = jobId
  try {
    await api.pauseJob(jobId)
    emit('refreshed')
  } finally {
    jobActionId.value = null
  }
}

async function resumeJob(jobId: number) {
  if (jobActionId.value != null) return
  jobActionId.value = jobId
  try {
    await api.resumeJob(jobId)
    emit('refreshed')
  } finally {
    jobActionId.value = null
  }
}

watch(
  () =>
    rows.value
      .map((row) => row.job?.id)
      .filter((id): id is number => id != null)
      .join(','),
  async () => {
    const openIds = [...logOpen.value]
    if (!openIds.length) return
    await Promise.all(
      openIds.map(async (jobId) => {
        try {
          logByJob.value = { ...logByJob.value, [jobId]: await api.getJobLog(jobId) }
        } catch {
          /* keep last log */
        }
      }),
    )
  },
)

function jobHref(jobId: number) {
  return withReturnTo(`/jobs/${jobId}`, route.fullPath)
}

function statsHref(jobId: number) {
  const base = withReturnTo(`/jobs/${jobId}`, route.fullPath)
  if (typeof base === 'string') return { path: base, hash: '#usage' }
  return { ...base, hash: '#usage' }
}
</script>

<template>
  <div class="rounded-md border border-l-4 border-ink-200 border-l-amber-500 bg-white p-4 dark:border-ink-800 dark:bg-ink-900">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <h2 class="font-display text-lg font-semibold">Live work</h2>
      <p v-if="rows.length === 1" class="font-mono text-sm text-ink-500">
        {{ progressSummary(rows[0]) }}
      </p>
    </div>

    <p v-if="!rows.length" class="mt-4 text-sm text-ink-500">No active localization.</p>

    <ul v-else class="mt-4 space-y-3">
      <li
        v-for="{ task, job } in rows"
        :key="task.id"
        class="rounded-md border p-3"
        :class="
          isDubTask(task)
            ? 'border-sky-200 bg-sky-50/60 dark:border-sky-900/70 dark:bg-sky-950/20'
            : 'border-ink-200 bg-ink-50/50 dark:border-ink-800 dark:bg-ink-950/40'
        "
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <RouterLink
              v-if="showTitle"
              class="block truncate font-medium text-accent hover:underline"
              :to="mediaHref(task.media_item_id)"
            >
              {{ localizationTaskTitle(task) }}
            </RouterLink>
            <p class="text-sm" :class="showTitle ? 'mt-0.5 text-ink-600 dark:text-ink-300' : 'font-medium'">
              {{ task.target_language_name }}
              <span class="font-normal text-ink-500">({{ task.target_language_code }})</span>
              <span class="text-ink-500"> · {{ capabilityLabel(task) }}</span>
            </p>
            <p class="mt-1 text-sm text-accent">
              {{ taskStatusLabel(task.status, task.substate) }}
              <span class="capitalize text-ink-500"> · {{ task.origin }}</span>
            </p>
            <p v-if="task.error_message" class="mt-1 max-w-2xl text-xs text-red-700 dark:text-red-300">
              {{ task.error_message }}
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button
              v-if="job && canPauseJob(job.status)"
              type="button"
              class="rounded-md border border-ink-300 px-2.5 py-1 text-xs font-semibold dark:border-ink-600"
              :disabled="busy || jobActionId === job.id"
              @click="pauseJob(job.id)"
            >
              Pause
            </button>
            <button
              v-else-if="job && canResumeJob(job.status)"
              type="button"
              class="rounded-md border border-ink-300 px-2.5 py-1 text-xs font-semibold dark:border-ink-600"
              :disabled="busy || jobActionId === job.id"
              @click="resumeJob(job.id)"
            >
              Resume
            </button>
            <button
              type="button"
              class="rounded-md border border-red-300 px-2.5 py-1 text-xs font-semibold text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-300"
              title="Cancel"
              aria-label="Cancel"
              :disabled="busy"
              @click="emit('cancel', task.id)"
            >
              Cancel
            </button>
          </div>
        </div>

        <ol v-if="task.progress_steps?.length" class="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
          <li v-for="step in task.progress_steps" :key="step.id" :class="stepClass(step.state)">
            {{ step.label }}
          </li>
        </ol>

        <div v-if="job" class="mt-3 text-sm">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <RouterLink class="capitalize text-accent hover:underline" :to="jobHref(job.id)">
              {{ jobKindLabel(job.job_kind) }} #{{ job.id }}
              <span :class="jobStatusClass(job.status)"> · {{ job.status }}</span>
              <span v-if="job.model" class="normal-case text-ink-500"> · {{ job.model }}</span>
            </RouterLink>
            <div class="flex shrink-0 items-center">
              <p v-if="rows.length > 1" class="mr-2 font-mono text-ink-600 dark:text-ink-300">
                {{ progressSummary({ task, job }) }}
              </p>
              <template v-if="jobHasAiArtifacts(job.job_kind)">
                <button
                  type="button"
                  :class="[iconBtnClass, logOpen.has(job.id) ? 'text-accent' : '']"
                  :disabled="logBusyId === job.id"
                  :title="logOpen.has(job.id) ? 'Hide log' : 'View logs'"
                  :aria-label="logOpen.has(job.id) ? 'Hide log' : 'View logs'"
                  @click="toggleLog(job.id)"
                >
                  <svg
                    class="h-4 w-4"
                    :class="logBusyId === job.id ? 'animate-pulse' : ''"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                    <path d="M16 13H8" />
                    <path d="M16 17H8" />
                    <path d="M10 9H8" />
                  </svg>
                </button>
                <RouterLink
                  :class="iconBtnClass"
                  :to="statsHref(job.id)"
                  title="Usage stats"
                  aria-label="Usage stats"
                >
                  <svg
                    class="h-4 w-4"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M18 20V10" />
                    <path d="M12 20V4" />
                    <path d="M6 20v-6" />
                  </svg>
                </RouterLink>
              </template>
            </div>
          </div>
          <p class="mt-1 text-ink-600 dark:text-ink-300">
            {{ job.progress_detail || `${job.progress}%` }}
          </p>
          <div
            v-if="job.status === 'pending' || job.status === 'processing'"
            class="mt-2 h-1.5 overflow-hidden rounded-md bg-ink-200 dark:bg-ink-800"
          >
            <div
              class="h-full bg-accent transition-all"
              :style="{ width: `${Math.min(100, Math.max(2, job.progress || 0))}%` }"
            />
          </div>
          <p
            v-if="logOpen.has(job.id) && logErrorByJob[job.id]"
            class="mt-2 text-xs text-red-700 dark:text-red-300"
          >
            {{ logErrorByJob[job.id] }}
          </p>
          <p
            v-else-if="logOpen.has(job.id) && logByJob[job.id] && !logByJob[job.id]?.exists"
            class="mt-2 text-xs text-ink-500"
          >
            No log file yet. Translation logs appear once the AI provider is called.
          </p>
          <pre
            v-else-if="logOpen.has(job.id) && formatJobLog(logByJob[job.id] || null)"
            class="mt-2 max-h-64 overflow-auto rounded-md bg-ink-950 p-3 text-xs leading-relaxed text-ink-100"
          >{{ formatJobLog(logByJob[job.id] || null) }}</pre>
        </div>
        <p v-else class="mt-3 text-sm text-ink-500">
          <template v-if="isDubTask(task)">Dubbing will start shortly.</template>
          <template v-else>Subtitle processing will start shortly.</template>
        </p>
      </li>
    </ul>
  </div>
</template>
