<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { AiOverview, AutomationStatus, Candidate, Health, Job } from '../types'
import { formatDateTime } from '../utils/datetime'

const store = useAppStore()
const health = ref<Health | null>(null)
const automation = ref<AutomationStatus | null>(null)
const aiOverview = ref<AiOverview | null>(null)
const pipelineLoading = ref(false)
const pipelineError = ref<string | null>(null)
const pipelineLoaded = ref(false)
let timer: number | undefined

const LIST_LIMIT = 8

function isTargetDone(item: Candidate) {
  return item.reason_code === 'target_exists'
}

function canRequestSource(item: Candidate) {
  if (isTargetDone(item)) return false
  if (item.active_request_job_id != null) return true
  if (item.source_subtitle_path) return false
  if (item.media_type === 'movie') return item.bazarr_movie_id != null
  return item.bazarr_episode_id != null && item.bazarr_series_id != null
}

const openCandidates = computed(() => store.candidates.filter((item) => !isTargetDone(item)))

const candidateHealth = computed(() => {
  const open = openCandidates.value
  return {
    missing: open.length,
    ready: open.filter((item) => item.can_translate && !item.active_translate_job_id).length,
    waitingGrace: open.filter((item) => item.reason_code === 'grace_period').length,
    waitingRetry: open.filter((item) =>
      ['retry_wait', 'automatic_retry', 'awaiting_retry'].includes(item.reason_code || ''),
    ).length,
    noSource: open.filter((item) => canRequestSource(item) && !item.active_request_job_id).length,
  }
})

function jobSortKey(job: Job) {
  return job.completed_at || job.started_at || job.created_at || ''
}

const failedJobs = computed(() =>
  store.jobs
    .filter((job) => job.status === 'failed')
    .slice()
    .sort((a, b) => jobSortKey(b).localeCompare(jobSortKey(a)))
    .slice(0, LIST_LIMIT),
)

const runningJobs = computed(() => {
  const processing = store.jobs
    .filter((job) => job.status === 'processing')
    .slice()
    .sort((a, b) => jobSortKey(b).localeCompare(jobSortKey(a)))
  const pending = store.jobs
    .filter((job) => job.status === 'pending')
    .slice()
    .sort((a, b) => jobSortKey(b).localeCompare(jobSortKey(a)))
  return [...processing, ...pending].slice(0, LIST_LIMIT)
})

const completedToday = computed(() => {
  const today = new Date()
  const key = today.toISOString().slice(0, 10)
  return store.jobs.filter((job) => {
    if (job.status !== 'completed' || !job.completed_at) return false
    return job.completed_at.slice(0, 10) === key
  }).length
})

const bazarrOk = computed(() => {
  if (health.value) return health.value.bazarr === 'configured'
  return Boolean(store.settings?.bazarr_url && store.settings.bazarr_api_key_configured)
})

const openRouterOk = computed(() => {
  if (health.value) return health.value.openrouter === 'configured'
  return Boolean(store.settings?.openrouter_api_key_configured)
})

function jobTitle(job: Job) {
  return job.media_title || job.media_path || `Job #${job.id}`
}

function formatUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n === 0) return '$0'
  return `$${n.toFixed(3)}`
}

function formatPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${(n * 100).toFixed(1)}%`
}

async function loadPipeline(refresh = false) {
  pipelineLoading.value = true
  pipelineError.value = null
  try {
    if (refresh) {
      await store.loadCandidates()
    } else {
      await store.loadCandidatesCached()
    }
    pipelineLoaded.value = true
  } catch (err) {
    pipelineError.value = err instanceof Error ? err.message : String(err)
  } finally {
    pipelineLoading.value = false
  }
}

async function loadHealth() {
  try {
    health.value = await api.getHealth()
  } catch {
    health.value = null
  }
}

async function loadAutomation() {
  try {
    automation.value = await api.getAutomationStatus()
  } catch {
    automation.value = null
  }
}

async function loadAiSummary() {
  try {
    aiOverview.value = await api.getAiOverview('month')
  } catch {
    aiOverview.value = null
  }
}

onMounted(async () => {
  await Promise.all([
    store.loadSettings().catch(() => undefined),
    store.loadJobs().catch(() => undefined),
    loadHealth(),
    loadAutomation(),
    loadAiSummary(),
    loadPipeline(false),
  ])
  timer = window.setInterval(() => {
    store.loadJobs().catch(() => undefined)
  }, 3000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="space-y-8">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="font-display text-2xl font-bold sm:text-3xl">Dashboard</h1>
        <p class="mt-1 text-sm text-ink-600 sm:text-base dark:text-ink-300">
          What Subtitle AI is doing right now.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          :disabled="pipelineLoading || store.loading"
          @click="loadPipeline(true)"
        >
          {{ pipelineLoading || store.loading ? 'Refreshing…' : 'Refresh' }}
        </button>
        <RouterLink
          class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90"
          to="/ai/overview"
        >
          Open AI Dashboard
        </RouterLink>
      </div>
    </div>

    <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
      <h2 class="font-display text-lg font-semibold">Automation</h2>
      <div class="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <div>
          Automatic fallback:
          <span class="font-semibold">
            {{ store.settings?.automatic_fallback_enabled ? '● Enabled' : '○ Disabled' }}
          </span>
        </div>
        <div class="text-ink-600 dark:text-ink-300">
          Last scan: {{ automation?.last_scan_at ? formatDateTime(automation.last_scan_at) : 'never' }}
        </div>
        <div>Candidates: {{ pipelineLoaded ? candidateHealth.missing : '—' }}</div>
        <div>Processing: {{ store.stats?.processing ?? 0 }}</div>
        <div>Completed today: {{ completedToday }}</div>
      </div>
    </section>

    <div class="grid gap-6 lg:grid-cols-2">
      <div class="space-y-3">
        <div class="flex items-baseline justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">Current jobs</h2>
          <RouterLink class="text-xs font-medium text-accent hover:underline" to="/jobs">View all</RouterLink>
        </div>
        <div class="rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
          <p v-if="!runningJobs.length" class="px-4 py-8 text-center text-sm text-ink-500">
            Queue is empty.
          </p>
          <ul v-else class="divide-y divide-ink-100 dark:divide-ink-800">
            <li v-for="job in runningJobs" :key="`run-${job.id}`" class="px-4 py-3">
              <RouterLink class="font-medium text-accent hover:underline" :to="`/jobs/${job.id}`">
                {{ jobTitle(job) }}
              </RouterLink>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
                <span class="capitalize">{{ job.job_kind || 'translate' }}</span>
                <template v-if="job.status === 'processing'">
                  <span>{{ job.progress }}%</span>
                  <span v-if="job.progress_detail" class="truncate">{{ job.progress_detail }}</span>
                </template>
                <template v-else>
                  <span>Pending</span>
                  <span>{{ formatDateTime(job.created_at) }}</span>
                </template>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <div class="space-y-3">
        <h2 class="font-display text-lg font-semibold">Candidate health</h2>
        <p v-if="pipelineError" class="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
          {{ pipelineError }}
        </p>
        <div class="grid grid-cols-2 gap-2 sm:gap-3">
          <RouterLink class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60" to="/candidates">
            <div class="text-xs uppercase text-ink-500">Missing subtitles</div>
            <div class="mt-1 font-display text-2xl font-bold">{{ candidateHealth.missing }}</div>
          </RouterLink>
          <RouterLink class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60" to="/candidates?filter=ready">
            <div class="text-xs uppercase text-ink-500">Ready to translate</div>
            <div class="mt-1 font-display text-2xl font-bold">{{ candidateHealth.ready }}</div>
          </RouterLink>
          <div class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
            <div class="text-xs uppercase text-ink-500">Waiting for grace</div>
            <div class="mt-1 font-display text-2xl font-bold">{{ candidateHealth.waitingGrace }}</div>
          </div>
          <RouterLink class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60" to="/candidates?filter=need-source">
            <div class="text-xs uppercase text-ink-500">No source</div>
            <div class="mt-1 font-display text-2xl font-bold">{{ candidateHealth.noSource }}</div>
          </RouterLink>
        </div>
      </div>
    </div>

    <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="font-display text-lg font-semibold">AI</h2>
        <RouterLink class="text-sm font-semibold text-accent hover:underline" to="/ai/overview">
          Open AI Dashboard
        </RouterLink>
      </div>
      <dl class="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-5">
        <div>
          <dt class="text-xs uppercase text-ink-500">This month</dt>
          <dd class="font-semibold">{{ formatUsd(aiOverview?.ai_summary?.this_month_cost_usd ?? aiOverview?.cards?.month?.cost_usd) }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase text-ink-500">Requests</dt>
          <dd class="font-semibold">{{ aiOverview?.ai_summary?.this_month_requests ?? aiOverview?.cards?.month?.requests ?? '—' }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase text-ink-500">Clean success</dt>
          <dd class="font-semibold">{{ formatPct(aiOverview?.ai_summary?.clean_success_rate ?? aiOverview?.clean_success_rate) }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase text-ink-500">Budget</dt>
          <dd class="font-semibold">
            <template v-if="aiOverview?.budget.enabled">
              {{ (aiOverview.budget.percent_used || 0).toFixed(1) }}% used
            </template>
            <template v-else>Off</template>
          </dd>
        </div>
        <div>
          <dt class="text-xs uppercase text-ink-500">Best observed</dt>
          <dd class="truncate font-semibold" :title="aiOverview?.ai_summary?.best_model_id || ''">
            {{ aiOverview?.ai_summary?.best_model_id || '—' }}
          </dd>
        </div>
      </dl>
    </section>

    <div class="grid gap-6 lg:grid-cols-2">
      <div class="space-y-3">
        <h2 class="font-display text-lg font-semibold">Needs attention</h2>
        <div class="rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
          <p v-if="!failedJobs.length" class="px-4 py-8 text-center text-sm text-ink-500">
            No failed jobs.
          </p>
          <ul v-else class="divide-y divide-ink-100 dark:divide-ink-800">
            <li v-for="job in failedJobs" :key="`fail-${job.id}`" class="px-4 py-3">
              <RouterLink class="font-medium text-accent hover:underline" :to="`/jobs/${job.id}`">
                {{ jobTitle(job) }}
              </RouterLink>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
                <span class="capitalize">{{ job.job_kind || 'translate' }}</span>
                <span>{{ formatDateTime(job.completed_at || job.created_at) }}</span>
              </div>
              <p v-if="job.error" class="mt-1 line-clamp-2 text-xs text-red-700 dark:text-red-300">
                {{ job.error }}
              </p>
            </li>
          </ul>
        </div>
      </div>

      <div class="space-y-3">
        <h2 class="font-display text-lg font-semibold">System</h2>
        <div class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <dl class="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt class="text-xs uppercase text-ink-500">Bazarr</dt>
              <dd class="font-medium" :class="bazarrOk ? '' : 'text-red-700 dark:text-red-300'">
                {{ bazarrOk ? 'Configured' : 'Not configured' }}
              </dd>
            </div>
            <div>
              <dt class="text-xs uppercase text-ink-500">OpenRouter</dt>
              <dd class="font-medium" :class="openRouterOk ? '' : 'text-red-700 dark:text-red-300'">
                {{ openRouterOk ? 'Configured' : 'Not configured' }}
              </dd>
            </div>
          </dl>
          <p v-if="!bazarrOk || !openRouterOk" class="mt-3 text-sm text-ink-600 dark:text-ink-300">
            <RouterLink v-if="!bazarrOk" class="font-medium text-accent hover:underline" to="/settings">Open settings</RouterLink>
            <template v-if="!bazarrOk && !openRouterOk"> · </template>
            <RouterLink v-if="!openRouterOk" class="font-medium text-accent hover:underline" to="/ai/models">Configure AI</RouterLink>
          </p>
        </div>
      </div>
    </div>
  </section>
</template>
