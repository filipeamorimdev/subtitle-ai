<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import { onLiveEvent } from '../stores/events'
import type {
  AiOverview,
  AutomationStatus,
  Candidate,
  Health,
  LocalizationTask,
} from '../types'
import { formatDateTime, formatElapsedClock } from '../utils/datetime'
import { localizationTaskTitle, mediaHref } from '../utils/mediaNav'
import { isActiveTaskStatus, taskStatusIcon, taskStatusLabel } from '../utils/status'
import { latestActiveJob, taskProgressPct } from '../utils/taskProgress'

const store = useAppStore()
const health = ref<Health | null>(null)
const automation = ref<AutomationStatus | null>(null)
const aiOverview = ref<AiOverview | null>(null)
const tasks = ref<LocalizationTask[]>([])
const currentTasksDetailed = ref<LocalizationTask[]>([])
const recentTranslations = ref<LocalizationTask[]>([])
const pipelineLoading = ref(false)
const pipelineError = ref<string | null>(null)
const pipelineLoaded = ref(false)
const modalOpen = ref(false)
const now = ref(Date.now())
let timer: number | undefined
let tick: number | undefined
let stopLive: (() => void) | undefined

const LIST_LIMIT = 5

function isTargetDone(item: Candidate) {
  return item.reason_code === 'target_exists'
}

const openCandidates = computed(() => store.candidates.filter((item) => !isTargetDone(item)))

const candidateHealth = computed(() => {
  const open = openCandidates.value
  return {
    missing: open.length,
    ready: open.filter((item) => item.can_translate && !item.active_translate_job_id).length,
  }
})

const currentLocalization = computed(() => {
  const byId = new Map(currentTasksDetailed.value.map((task) => [task.id, task]))
  return tasks.value
    .filter((task) => isActiveTaskStatus(task.status))
    .slice(0, LIST_LIMIT)
    .map((task) => byId.get(task.id) || task)
})

function latestJob(task: LocalizationTask) {
  return latestActiveJob(task)
}

function taskElapsed(task: LocalizationTask) {
  const job = latestJob(task)
  const start = job?.started_at || task.started_at || job?.created_at || task.created_at
  return formatElapsedClock(start, now.value)
}

const failedTasks = computed(() =>
  tasks.value.filter((t) => t.status === 'failed').slice(0, LIST_LIMIT),
)

const completedToday = computed(() => {
  const key = new Date().toISOString().slice(0, 10)
  return tasks.value.filter(
    (t) => t.status === 'completed' && t.completed_at?.slice(0, 10) === key,
  ).length
})

const bazarrOk = computed(() => {
  if (health.value) return health.value.bazarr === 'configured' || health.value.bazarr === 'ok'
  return Boolean(store.settings?.bazarr_url && store.settings.bazarr_api_key_configured)
})

const openRouterOk = computed(() => {
  if (health.value) {
    return health.value.openrouter === 'configured' || health.value.openrouter === 'ok'
  }
  return Boolean(store.settings?.openrouter_api_key_configured)
})

const monthCost = computed(
  () => aiOverview.value?.ai_summary?.this_month_cost_usd ?? aiOverview.value?.cards?.month?.cost_usd,
)
const monthRequests = computed(
  () => aiOverview.value?.ai_summary?.this_month_requests ?? aiOverview.value?.cards?.month?.requests,
)
const cleanSuccess = computed(
  () => aiOverview.value?.ai_summary?.clean_success_rate ?? aiOverview.value?.clean_success_rate,
)
const bestModel = computed(() => aiOverview.value?.ai_summary?.best_model_id || null)
const budgetPct = computed(() => aiOverview.value?.budget.percent_used)

const attentionReasons = computed(() => {
  const reasons: string[] = []
  if (!bazarrOk.value) {
    reasons.push(
      health.value?.bazarr === 'unreachable'
        ? 'Bazarr is unreachable.'
        : 'Bazarr is not configured.',
    )
  }
  if (health.value?.planner_error) reasons.push('Localization planner failed to resume after restart.')
  if (!openRouterOk.value) reasons.push('OpenRouter is not configured.')
  if (aiOverview.value?.status === 'attention') {
    const aiReasons = aiOverview.value.status_reasons?.filter(Boolean) || []
    if (aiReasons.length) reasons.push(...aiReasons)
    else reasons.push('AI needs attention.')
  }
  if (failedTasks.value.length) {
    reasons.push(
      `${failedTasks.value.length} failed localization${failedTasks.value.length === 1 ? '' : 's'}.`,
    )
  }
  return reasons
})

const heroTone = computed(() => {
  if (attentionReasons.value.length) return 'attention'
  if (!currentLocalization.value.length && (aiOverview.value?.status === 'idle' || !aiOverview.value)) {
    return 'idle'
  }
  return 'healthy'
})

const hasNeedsAttention = computed(
  () =>
    Boolean(pipelineError.value) ||
    !bazarrOk.value ||
    !openRouterOk.value ||
    failedTasks.value.length > 0,
)

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

async function refreshDashboard() {
  await Promise.all([
    loadPipeline(true),
    loadTasks(),
    loadCurrentLocalization(),
    loadRecentTranslations(),
    loadHealth(),
    loadAutomation(),
    loadAiSummary(),
  ])
}

async function loadTasks() {
  try {
    tasks.value = await api.getLocalizationTasks({ limit: 100 })
  } catch {
    /* keep previous */
  }
}

async function loadCurrentLocalization() {
  try {
    currentTasksDetailed.value = await api.getLocalizationTasks({
      active_only: true,
      include_detail: true,
      limit: LIST_LIMIT,
    })
  } catch {
    /* keep previous */
  }
}

async function loadRecentTranslations() {
  try {
    recentTranslations.value = await api.getLocalizationTasks({
      status: 'completed',
      sort: 'completed_at',
      limit: 4,
    })
  } catch {
    /* keep previous */
  }
}

async function loadHealth() {
  try {
    health.value = await api.getHealth(true)
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
  await store.loadSettings().catch(() => undefined)
  await Promise.all([
    loadHealth(),
    loadAutomation(),
    loadAiSummary(),
    loadPipeline(false),
    loadTasks(),
    loadCurrentLocalization(),
    loadRecentTranslations(),
  ])
  tick = window.setInterval(() => {
    now.value = Date.now()
  }, 1000)
  timer = window.setInterval(() => {
    loadTasks().catch(() => undefined)
    loadCurrentLocalization().catch(() => undefined)
    loadRecentTranslations().catch(() => undefined)
    loadHealth().catch(() => undefined)
  }, 30000)
  stopLive = onLiveEvent((event) => {
    if (event.type === 'hello') return
    loadTasks().catch(() => undefined)
    loadCurrentLocalization().catch(() => undefined)
    loadRecentTranslations().catch(() => undefined)
  })
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  if (tick) window.clearInterval(tick)
  stopLive?.()
})
</script>

<template>
  <section class="space-y-8">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="font-display text-2xl font-bold sm:text-3xl">
          <span class="mr-1 inline-block origin-bottom-right" :class="heroTone === 'healthy' ? 'dash-wiggle' : ''">🎬</span>
          Dashboard
        </h1>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="inline-flex items-center gap-1.5 rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          :disabled="pipelineLoading || store.loading"
          @click="refreshDashboard"
        >
          <span :class="pipelineLoading || store.loading ? 'inline-block animate-spin' : ''">🔄</span>
          {{ pipelineLoading || store.loading ? 'Refreshing…' : 'Refresh' }}
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90"
          @click="modalOpen = true"
        >
          ✨ Request subtitles
        </button>
      </div>
    </div>

    <div class="grid items-stretch gap-6" :class="hasNeedsAttention ? 'lg:grid-cols-2' : ''">
      <section
        class="rounded-2xl border px-5 py-4 shadow-sm"
        :class="{
          'border-emerald-300 bg-gradient-to-br from-emerald-50 to-lime-50 dark:border-emerald-800 dark:from-emerald-950/50 dark:to-ink-900/60':
            heroTone === 'healthy',
          'border-sky-200 bg-gradient-to-br from-sky-50 to-indigo-50 dark:border-sky-900 dark:from-sky-950/40 dark:to-ink-900/60':
            heroTone === 'idle',
          'border-amber-300 bg-gradient-to-br from-amber-50 to-orange-50 dark:border-amber-800 dark:from-amber-950/40 dark:to-ink-900/60':
            heroTone === 'attention',
        }"
      >
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div class="flex items-start gap-3">
            <span
              class="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl text-2xl shadow-inner"
              :class="{
                'dash-wiggle bg-emerald-200/80 dark:bg-emerald-900/70': heroTone === 'healthy',
                'dash-bob bg-sky-200/80 dark:bg-sky-900/70': heroTone === 'idle',
                'dash-wiggle bg-amber-200/80 dark:bg-amber-900/70': heroTone === 'attention',
              }"
              aria-hidden="true"
            >
              {{ heroTone === 'attention' ? '😬' : heroTone === 'idle' ? '😴' : '🥳' }}
            </span>
            <div>
              <div class="text-xs uppercase tracking-wide text-ink-500">Status</div>
              <div class="mt-1 font-display text-2xl font-bold">
                <span v-if="heroTone === 'attention'">Uh-oh, attention needed</span>
                <span v-else-if="heroTone === 'idle'">Idle — catching a nap</span>
                <span v-else>Looking spicy</span>
              </div>
              <ul v-if="attentionReasons.length" class="mt-2 space-y-1 text-sm text-ink-700 dark:text-ink-200">
                <li v-for="reason in attentionReasons" :key="reason">• {{ reason }}</li>
              </ul>
              <p v-else class="mt-2 text-sm text-ink-600 dark:text-ink-300">
                Automation
                {{ store.settings?.automatic_fallback_enabled ? 'on' : 'off' }}
                · last scan
                {{ automation?.last_scan_at ? formatDateTime(automation.last_scan_at) : 'never' }}
              </p>
            </div>
          </div>
          <dl class="grid grid-cols-2 gap-2 text-sm" :class="hasNeedsAttention ? '' : 'sm:grid-cols-4'">
            <div class="rounded-xl bg-white/70 px-3 py-2 dark:bg-ink-950/40">
              <dt class="text-xs uppercase text-ink-500">⚙️ Automation</dt>
              <dd class="font-semibold">
                {{ store.settings?.automatic_fallback_enabled ? 'Enabled' : 'Disabled' }}
              </dd>
            </div>
            <div class="rounded-xl bg-white/70 px-3 py-2 dark:bg-ink-950/40">
              <dt class="text-xs uppercase text-ink-500">🏃 Active tasks</dt>
              <dd class="font-semibold">{{ currentLocalization.length }}</dd>
            </div>
            <div class="rounded-xl bg-white/70 px-3 py-2 dark:bg-ink-950/40">
              <dt class="text-xs uppercase text-ink-500">🎉 Completed today</dt>
              <dd class="font-semibold">{{ completedToday }}</dd>
            </div>
            <div class="rounded-xl bg-white/70 px-3 py-2 dark:bg-ink-950/40">
              <dt class="text-xs uppercase text-ink-500">🤖 AI jobs</dt>
              <dd class="font-semibold">{{ aiOverview?.active_jobs ?? 0 }}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section
        v-if="hasNeedsAttention"
        class="rounded-2xl border border-rose-200 bg-gradient-to-br from-rose-50/70 to-white px-5 py-4 shadow-sm dark:border-rose-900 dark:from-rose-950/30 dark:to-ink-900/60"
      >
        <h2 class="font-display text-lg font-semibold">🚨 Needs attention</h2>
        <p
          v-if="pipelineError"
          class="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {{ pipelineError }}
        </p>
        <ul
          v-if="!bazarrOk || !openRouterOk || failedTasks.length"
          class="mt-3 divide-y divide-rose-100 dark:divide-ink-800"
        >
          <li v-if="!bazarrOk" class="py-3 text-sm first:pt-0">
            🔌 Bazarr is not configured.
            <RouterLink class="ml-1 font-medium text-accent hover:underline" to="/settings/providers">
              Open settings
            </RouterLink>
          </li>
          <li v-if="!openRouterOk" class="py-3 text-sm first:pt-0">
            🔌 OpenRouter is not configured.
            <RouterLink class="ml-1 font-medium text-accent hover:underline" to="/settings/providers">
              Open settings
            </RouterLink>
          </li>
          <li v-for="task in failedTasks" :key="`fail-${task.id}`" class="py-3 first:pt-0">
            <RouterLink class="font-medium text-accent hover:underline" :to="mediaHref(task.media_item_id)">
              {{ task.media_title || `Media #${task.media_item_id}` }}
            </RouterLink>
            <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
              <span>{{ task.target_language_name }}</span>
              <span>{{ formatDateTime(task.completed_at || task.created_at) }}</span>
            </div>
            <p
              v-if="task.error_message"
              class="mt-1 line-clamp-2 text-xs text-red-700 dark:text-red-300"
            >
              {{ task.error_message }}
            </p>
          </li>
        </ul>
      </section>
    </div>

    <div class="grid items-stretch gap-3 lg:grid-cols-2 lg:gap-6">
      <div class="flex h-full flex-col space-y-3">
        <div class="flex items-baseline justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">🎙️ Current localization</h2>
          <RouterLink class="text-xs font-medium text-accent hover:underline" to="/media">
            View all
          </RouterLink>
        </div>
        <div class="flex-1 rounded-2xl border border-sky-200 bg-gradient-to-br from-sky-50/70 to-white shadow-sm dark:border-sky-900 dark:from-sky-950/30 dark:to-ink-900/60">
          <p v-if="!currentLocalization.length" class="px-4 py-8 text-center text-sm text-ink-500">
            Nobody's in the booth. Go grab popcorn. 🍿
          </p>
          <ul v-else class="divide-y divide-sky-100 dark:divide-ink-800">
            <li v-for="task in currentLocalization" :key="`cur-${task.id}`" class="px-4 py-3">
              <div class="flex items-start justify-between gap-3">
                <RouterLink
                  class="min-w-0 truncate font-medium text-accent hover:underline"
                  :to="mediaHref(task.media_item_id)"
                >
                  {{ localizationTaskTitle(task) }}
                </RouterLink>
                <p class="shrink-0 text-xs tabular-nums text-ink-500">
                  <span class="font-medium text-ink-700 dark:text-ink-200">{{ taskProgressPct(task) }}%</span>
                  <span class="mx-1.5 text-ink-300 dark:text-ink-600">·</span>
                  <span>{{ taskElapsed(task) }}</span>
                </p>
              </div>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
                <span>{{ task.target_language_name }}</span>
                <span>{{ taskStatusIcon(task.status) }} {{ taskStatusLabel(task.status, task.substate) }}</span>
                <span class="capitalize">{{ task.origin }}</span>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <div class="flex h-full flex-col space-y-3">
        <div class="flex items-baseline justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">✅ Last translations</h2>
          <RouterLink class="text-xs font-medium text-accent hover:underline" to="/translations">
            Show all
          </RouterLink>
        </div>
        <div class="flex-1 rounded-2xl border border-emerald-200 bg-gradient-to-br from-emerald-50/70 to-white shadow-sm dark:border-emerald-900 dark:from-emerald-950/30 dark:to-ink-900/60">
          <p v-if="!recentTranslations.length" class="px-4 py-8 text-center text-sm text-ink-500">
            No completed translations yet. 🎬
          </p>
          <ul v-else class="divide-y divide-emerald-100 dark:divide-ink-800">
            <li v-for="task in recentTranslations" :key="`done-${task.id}`" class="px-4 py-3">
              <RouterLink class="font-medium text-accent hover:underline" :to="mediaHref(task.media_item_id)">
                {{ localizationTaskTitle(task) }}
              </RouterLink>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
                <span>{{ task.target_language_name }}</span>
                <span>{{ formatDateTime(task.completed_at || task.updated_at) }}</span>
                <span class="capitalize">{{ task.origin }}</span>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-3">
        <RouterLink
          class="group flex h-full flex-col rounded-2xl border border-rose-200 bg-gradient-to-br from-rose-50 to-white px-4 py-3 shadow-sm transition hover:-translate-y-0.5 hover:border-rose-400 hover:shadow-md dark:border-rose-900 dark:from-rose-950/40 dark:to-ink-900/60"
          to="/media?filter=needs-work"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="text-[10px] uppercase tracking-wide text-rose-700 sm:text-xs dark:text-rose-300">
              Missing subtitles
            </div>
            <span class="flex h-9 w-9 items-center justify-center rounded-xl bg-rose-200 text-lg shadow-inner transition group-hover:rotate-6 dark:bg-rose-900/80">
              🙈
            </span>
          </div>
          <div class="mt-1 font-display text-2xl font-bold text-rose-800 dark:text-rose-200">
            {{ pipelineLoaded ? candidateHealth.missing : '—' }}
          </div>
        </RouterLink>
        <RouterLink
          class="group flex h-full flex-col rounded-2xl border border-sky-200 bg-gradient-to-br from-sky-50 to-white px-4 py-3 shadow-sm transition hover:-translate-y-0.5 hover:border-sky-400 hover:shadow-md dark:border-sky-900 dark:from-sky-950/40 dark:to-ink-900/60"
          to="/media?filter=needs-work"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="text-[10px] uppercase tracking-wide text-sky-700 sm:text-xs dark:text-sky-300">
              Ready to translate
            </div>
            <span class="flex h-9 w-9 items-center justify-center rounded-xl bg-sky-200 text-lg shadow-inner transition group-hover:rotate-6 dark:bg-sky-900/80">
              🚀
            </span>
          </div>
          <div class="mt-1 font-display text-2xl font-bold text-sky-800 dark:text-sky-200">
            {{ pipelineLoaded ? candidateHealth.ready : '—' }}
          </div>
        </RouterLink>
        <RouterLink
          class="group flex h-full flex-col rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-50 to-white px-4 py-3 shadow-sm transition hover:-translate-y-0.5 hover:border-violet-400 hover:shadow-md dark:border-violet-900 dark:from-violet-950/40 dark:to-ink-900/60"
          to="/ai/overview"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="text-[10px] uppercase tracking-wide text-violet-700 sm:text-xs dark:text-violet-300">
              This month
            </div>
            <span class="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-200 text-lg shadow-inner transition group-hover:rotate-6 dark:bg-violet-900/80">
              💸
            </span>
          </div>
          <div class="mt-1 font-display text-2xl font-bold text-violet-800 dark:text-violet-200">
            {{ formatUsd(monthCost) }}
          </div>
        </RouterLink>
    </div>

    <section class="rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-50/80 to-white p-5 shadow-sm dark:border-violet-900 dark:from-violet-950/30 dark:to-ink-900/60">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">🤖 AI brain</h2>
          <RouterLink class="text-sm font-semibold text-accent hover:underline" to="/ai/overview">
            Open AI dashboard →
          </RouterLink>
        </div>
        <dl class="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div class="rounded-xl bg-white/80 px-3 py-2 dark:bg-ink-950/40">
            <dt class="text-xs uppercase text-ink-500">💰 This month</dt>
            <dd class="font-display text-xl font-bold">{{ formatUsd(monthCost) }}</dd>
          </div>
          <div class="rounded-xl bg-white/80 px-3 py-2 dark:bg-ink-950/40">
            <dt class="text-xs uppercase text-ink-500">☀️ Today</dt>
            <dd class="font-display text-xl font-bold">
              {{ formatUsd(aiOverview?.cards?.today?.cost_usd) }}
            </dd>
          </div>
          <div class="rounded-xl bg-white/80 px-3 py-2 dark:bg-ink-950/40">
            <dt class="text-xs uppercase text-ink-500">📬 Requests</dt>
            <dd class="font-display text-xl font-bold">{{ monthRequests ?? '—' }}</dd>
          </div>
          <div class="rounded-xl bg-white/80 px-3 py-2 dark:bg-ink-950/40">
            <dt class="text-xs uppercase text-ink-500">✨ Clean success</dt>
            <dd class="font-display text-xl font-bold">{{ formatPct(cleanSuccess) }}</dd>
          </div>
          <div class="rounded-xl bg-white/80 px-3 py-2 dark:bg-ink-950/40">
            <dt class="text-xs uppercase text-ink-500">🏆 Best observed</dt>
            <dd class="truncate font-semibold" :title="bestModel || ''">{{ bestModel || '—' }}</dd>
          </div>
          <div class="rounded-xl bg-white/80 px-3 py-2 dark:bg-ink-950/40">
            <dt class="text-xs uppercase text-ink-500">📊 Budget</dt>
            <dd class="font-semibold">
              <template v-if="aiOverview?.budget.enabled">
                {{ (budgetPct || 0).toFixed(1) }}% used
              </template>
              <template v-else>Off</template>
            </dd>
          </div>
        </dl>
        <template v-if="aiOverview?.budget.enabled">
          <div class="mt-4 h-2.5 overflow-hidden rounded-full bg-ink-100 dark:bg-ink-800">
            <div
              class="h-full rounded-full bg-gradient-to-r from-violet-500 to-accent"
              :style="{ width: `${Math.min(100, budgetPct || 0)}%` }"
            />
          </div>
          <p class="mt-2 text-xs text-ink-500">
            {{ formatUsd(aiOverview.budget.used) }} / {{ formatUsd(aiOverview.budget.limit) }}
            · {{ formatUsd(aiOverview.budget.remaining) }} remaining
          </p>
        </template>
      </section>

    <RequestSubtitlesModal
      :open="modalOpen"
      @close="modalOpen = false"
      @created="
        () => {
          loadTasks()
          loadCurrentLocalization()
        }
      "
    />
  </section>
</template>

<style scoped>
@keyframes dash-wiggle {
  0%,
  100% {
    transform: rotate(-8deg);
  }
  50% {
    transform: rotate(8deg);
  }
}

@keyframes dash-bob {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-4px);
  }
}

.dash-wiggle {
  animation: dash-wiggle 1.6s ease-in-out infinite;
}

.dash-bob {
  animation: dash-bob 2.2s ease-in-out infinite;
}
</style>
