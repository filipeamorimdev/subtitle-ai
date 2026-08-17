<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import type { Job, JobLog, LocalizationTask } from '../types'
import { formatDateTime } from '../utils/datetime'
import { jobStatusClass, taskStatusLabel } from '../utils/status'

const props = defineProps<{
  tasks: LocalizationTask[]
  busy?: boolean
}>()

const emit = defineEmits<{
  cancel: [taskId: number]
}>()

interface RunningRow {
  task: LocalizationTask
  job: Job | null
}

const logByJob = ref<Record<number, JobLog | null>>({})
const logErrorByJob = ref<Record<number, string | null>>({})
const logOpen = ref<Set<number>>(new Set())
const logBusyId = ref<number | null>(null)

const rows = computed<RunningRow[]>(() =>
  props.tasks.map((task) => {
    const jobs = task.executions || []
    const job =
      [...jobs].reverse().find((item) => item.status === 'pending' || item.status === 'processing') ||
      jobs[jobs.length - 1] ||
      null
    return { task, job }
  }),
)

function stepClass(state: string) {
  if (state === 'done') return 'text-emerald-700 dark:text-emerald-300'
  if (state === 'active') return 'font-semibold text-accent'
  if (state === 'failed') return 'text-red-700 dark:text-red-300'
  if (state === 'skipped') return 'text-ink-400 line-through'
  return 'text-ink-400'
}

function formatLog(jobLog: JobLog | null) {
  if (!jobLog?.exists) return ''
  if (jobLog.entries?.length) {
    return jobLog.entries
      .map((entry) => {
        const normalized = { ...entry }
        if (typeof normalized.ts === 'string') {
          normalized.ts = formatDateTime(normalized.ts)
        }
        return JSON.stringify(normalized, null, 2)
      })
      .join('\n\n')
  }
  return jobLog.content || ''
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
</script>

<template>
  <div class="rounded-xl border border-accent/30 bg-accent/5 p-5 dark:bg-accent/10">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 class="font-display text-lg font-bold">Running</h2>
      </div>
      <p class="text-sm text-ink-500">{{ rows.length }} active</p>
    </div>

    <ul class="mt-4 space-y-4">
      <li
        v-for="{ task, job } in rows"
        :key="task.id"
        class="rounded-lg border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="font-medium">
              {{ task.target_language_name }}
              <span class="font-normal text-ink-500">({{ task.target_language_code }})</span>
            </p>
            <p class="mt-1 text-sm text-accent">
              {{ taskStatusLabel(task.status, task.substate) }}
              <span class="capitalize text-ink-500"> · {{ task.origin }}</span>
            </p>
          </div>
          <button
            type="button"
            class="rounded-md border border-red-300 px-3 py-1.5 text-sm font-semibold text-red-700 disabled:opacity-50 dark:border-red-800 dark:text-red-300"
            :disabled="busy"
            @click="emit('cancel', task.id)"
          >
            Cancel
          </button>
        </div>

        <ol v-if="task.progress_steps?.length" class="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
          <li v-for="step in task.progress_steps" :key="step.id" :class="stepClass(step.state)">
            {{ step.label }}
          </li>
        </ol>

        <div v-if="job" class="mt-3 text-sm">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <RouterLink class="capitalize text-accent hover:underline" :to="`/jobs/${job.id}`">
              {{ job.job_kind }} #{{ job.id }}
              <span :class="jobStatusClass(job.status)"> · {{ job.status }}</span>
            </RouterLink>
            <button
              type="button"
              class="rounded-md border border-ink-300 px-2 py-1 text-xs font-semibold dark:border-ink-600"
              :disabled="logBusyId === job.id"
              @click="toggleLog(job.id)"
            >
              {{
                logOpen.has(job.id) ? 'Hide log' : logBusyId === job.id ? 'Loading…' : 'View log'
              }}
            </button>
          </div>
          <p class="mt-1 text-ink-600 dark:text-ink-300">
            {{ job.progress_detail || `${job.progress}%` }}
          </p>
          <div
            v-if="job.status === 'pending' || job.status === 'processing'"
            class="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-200 dark:bg-ink-800"
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
            v-else-if="logOpen.has(job.id) && formatLog(logByJob[job.id] || null)"
            class="mt-2 max-h-64 overflow-auto rounded-lg bg-ink-950 p-3 text-xs leading-relaxed text-ink-100"
          >{{ formatLog(logByJob[job.id] || null) }}</pre>
        </div>
        <p v-else class="mt-3 text-sm text-ink-500">
          No execution has been queued yet. Subtitle AI will create the next job shortly.
        </p>
      </li>
    </ul>
  </div>
</template>
