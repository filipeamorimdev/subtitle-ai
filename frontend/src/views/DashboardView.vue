<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { Candidate, Health, Job } from '../types'
import { formatDateTime } from '../utils/datetime'

const store = useAppStore()
const health = ref<Health | null>(null)
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

const pipeline = computed(() => {
  const open = openCandidates.value
  return {
    ready: open.filter((item) => item.can_translate).length,
    extract: open.filter((item) => item.can_extract && !item.active_extract_job_id).length,
    needSource: open.filter((item) => canRequestSource(item) && !item.active_request_job_id).length,
    done: store.candidates.filter(isTargetDone).length,
    open: open.length,
  }
})

const jobStatusCards = computed(() => [
  {
    label: 'Pending',
    status: 'pending',
    count: store.stats?.pending ?? 0,
    tone: 'neutral' as const,
  },
  {
    label: 'Processing',
    status: 'processing',
    count: store.stats?.processing ?? 0,
    tone: 'active' as const,
  },
  {
    label: 'Failed',
    status: 'failed',
    count: store.stats?.failed ?? 0,
    tone: 'danger' as const,
  },
  {
    label: 'Completed',
    status: 'completed',
    count: store.stats?.completed ?? 0,
    tone: 'neutral' as const,
  },
])

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

const bazarrOk = computed(() => {
  if (health.value) return health.value.bazarr === 'configured'
  return Boolean(store.settings?.bazarr_url && store.settings.bazarr_api_key_configured)
})

const openRouterOk = computed(() => {
  if (health.value) return health.value.openrouter === 'configured'
  return Boolean(store.settings?.openrouter_api_key_configured)
})

const targetLabel = computed(() => {
  const lang = store.settings?.target_language
  if (!lang) return '—'
  return lang.name ? `${lang.name} (${lang.code})` : lang.code
})

const modelLabel = computed(() => store.settings?.openrouter_model || '—')

function cardClass(tone: 'neutral' | 'active' | 'danger', count: number) {
  if (tone === 'danger' && count > 0) {
    return 'border-red-300 bg-red-50/80 dark:border-red-900/60 dark:bg-red-950/30'
  }
  if (tone === 'active' && count > 0) {
    return 'border-accent/50 bg-accent/10 dark:bg-accent/20'
  }
  return 'border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60'
}

function jobTitle(job: Job) {
  return job.media_title || job.media_path || `Job #${job.id}`
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

onMounted(async () => {
  await Promise.all([
    store.loadSettings().catch(() => undefined),
    store.loadJobs().catch(() => undefined),
    loadHealth(),
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
        <h1 class="font-display text-2xl font-bold sm:text-3xl">Home</h1>
        <p class="mt-1 text-sm text-ink-600 sm:text-base dark:text-ink-300">
          Pipeline health and jobs that need attention.
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          :disabled="pipelineLoading || store.loading"
          @click="loadPipeline(true)"
        >
          {{ pipelineLoading || store.loading ? 'Refreshing…' : 'Refresh pipeline' }}
        </button>
        <RouterLink
          class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          to="/candidates"
        >
          Candidates
        </RouterLink>
        <RouterLink
          class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90"
          to="/jobs"
        >
          Jobs
        </RouterLink>
      </div>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <div class="space-y-3">
        <div class="flex items-baseline justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">Pipeline</h2>
          <span class="text-xs text-ink-500">
            <template v-if="pipelineLoaded">{{ pipeline.open }} open</template>
            <template v-else-if="pipelineLoading">Loading…</template>
          </span>
        </div>
        <p v-if="pipelineError" class="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
          {{ pipelineError }}
        </p>
        <div class="grid grid-cols-2 gap-2 sm:gap-3">
          <RouterLink
            class="rounded-xl border border-ink-200 bg-white/80 px-3 py-3 transition hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60 dark:hover:border-accent/50 sm:px-4"
            to="/candidates?filter=ready"
          >
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Ready to translate</div>
            <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ pipeline.ready }}</div>
          </RouterLink>
          <RouterLink
            class="rounded-xl border border-ink-200 bg-white/80 px-3 py-3 transition hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60 dark:hover:border-accent/50 sm:px-4"
            to="/candidates?filter=extract"
          >
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Can extract</div>
            <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ pipeline.extract }}</div>
          </RouterLink>
          <RouterLink
            class="rounded-xl border border-ink-200 bg-white/80 px-3 py-3 transition hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60 dark:hover:border-accent/50 sm:px-4"
            to="/candidates?filter=need-source"
          >
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Need source</div>
            <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ pipeline.needSource }}</div>
          </RouterLink>
          <RouterLink
            class="rounded-xl border border-ink-200 bg-white/80 px-3 py-3 transition hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60 dark:hover:border-accent/50 sm:px-4"
            to="/candidates?filter=target-exists"
          >
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Target exists</div>
            <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ pipeline.done }}</div>
          </RouterLink>
        </div>
      </div>

      <div class="space-y-3">
        <div class="flex items-baseline justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">Jobs now</h2>
          <RouterLink class="text-xs font-medium text-accent hover:underline" to="/jobs">View all</RouterLink>
        </div>
        <div class="grid grid-cols-2 gap-2 sm:gap-3">
          <RouterLink
            v-for="item in jobStatusCards"
            :key="item.status"
            class="rounded-xl border px-3 py-3 transition hover:border-accent/50 sm:px-4"
            :class="cardClass(item.tone, item.count)"
            to="/jobs"
          >
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">{{ item.label }}</div>
            <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ item.count }}</div>
          </RouterLink>
        </div>
      </div>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <div class="space-y-3">
        <h2 class="font-display text-lg font-semibold">Needs attention</h2>
        <div class="rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
          <p
            v-if="!failedJobs.length"
            class="px-4 py-8 text-sm text-ink-500"
          >
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
        <h2 class="font-display text-lg font-semibold">Running</h2>
        <div class="rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
          <p
            v-if="!runningJobs.length"
            class="px-4 py-8 text-sm text-ink-500"
          >
            Nothing in the queue.
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
    </div>

    <div class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="font-display text-lg font-semibold">System</h2>
        <span v-if="health?.version" class="text-xs text-ink-500">v{{ health.version }}</span>
      </div>
      <dl class="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt class="text-xs uppercase tracking-wide text-ink-500">Bazarr</dt>
          <dd class="mt-0.5 font-medium" :class="bazarrOk ? 'text-ink-900 dark:text-ink-50' : 'text-red-700 dark:text-red-300'">
            {{ bazarrOk ? 'Configured' : 'Not configured' }}
          </dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-ink-500">OpenRouter</dt>
          <dd class="mt-0.5 font-medium" :class="openRouterOk ? 'text-ink-900 dark:text-ink-50' : 'text-red-700 dark:text-red-300'">
            {{ openRouterOk ? 'Configured' : 'Not configured' }}
          </dd>
        </div>
        <div class="min-w-0">
          <dt class="text-xs uppercase tracking-wide text-ink-500">Target</dt>
          <dd class="mt-0.5 truncate font-medium" :title="targetLabel">{{ targetLabel }}</dd>
        </div>
        <div class="min-w-0">
          <dt class="text-xs uppercase tracking-wide text-ink-500">Model</dt>
          <dd class="mt-0.5 truncate font-medium" :title="modelLabel">{{ modelLabel }}</dd>
        </div>
      </dl>
      <p v-if="!bazarrOk || !openRouterOk" class="mt-3 text-sm text-ink-600 dark:text-ink-300">
        <RouterLink class="font-medium text-accent hover:underline" to="/settings">Open settings</RouterLink>
        to finish setup.
      </p>
    </div>
  </section>
</template>
