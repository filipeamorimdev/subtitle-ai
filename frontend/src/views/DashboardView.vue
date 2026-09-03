<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import RequestDubModal from '../components/RequestDubModal.vue'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import OperatorChatBar from '../components/OperatorChatBar.vue'
import RunningJobsPanel from '../components/RunningJobsPanel.vue'
import AiOverviewView from './ai/AiOverviewView.vue'
import AiUsageView from './ai/AiUsageView.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import { onLiveEvent } from '../stores/events'
import type {
  AiOverview,
  AutomationStatus,
  Candidate,
  Health,
  Job,
  LocalizationTask,
} from '../types'
import { formatDateTime } from '../utils/datetime'
import { localizationTaskTitle, mediaHref } from '../utils/mediaNav'
import { isOpenJobStatus } from '../utils/taskProgress'
import {
  canRetryTask,
  isUnresolvedFailedTask,
  jobKindLabel,
  pipelineStage,
  type PipelineStage,
} from '../utils/status'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

const health = ref<Health | null>(null)
const automation = ref<AutomationStatus | null>(null)
const aiOverview = ref<AiOverview | null>(null)
const tasks = ref<LocalizationTask[]>([])
const currentTasksDetailed = ref<LocalizationTask[]>([])
const completedJobs = ref<Job[]>([])
const pipelineLoading = ref(false)
const pipelineError = ref<string | null>(null)
const pipelineLoaded = ref(false)
const modalOpen = ref(false)
const dubModalOpen = ref(false)
const scanning = ref(false)
const actionBusyId = ref<number | null>(null)
const actionError = ref<string | null>(null)
const moneyPeriod = ref<'today' | '7d' | 'month'>('month')
let timer: number | undefined
let stopLive: (() => void) | undefined

const LIVE_LIMIT = 20

type DashTab = 'ops' | 'ai'
type AiReport = 'overview' | 'usage'

const activeTab = computed<DashTab>(() => (route.query.tab === 'ai' ? 'ai' : 'ops'))
const aiReport = computed<AiReport>(() => (route.query.report === 'usage' ? 'usage' : 'overview'))

function setTab(tab: DashTab) {
  const query = { ...route.query }
  if (tab === 'ops') {
    delete query.tab
    delete query.report
  } else {
    query.tab = 'ai'
  }
  router.replace({ query })
}

function setAiReport(report: AiReport) {
  const query: Record<string, string> = { ...route.query as Record<string, string>, tab: 'ai' }
  if (report === 'overview') delete query.report
  else query.report = 'usage'
  router.replace({ query })
}

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

const detailedById = computed(() => {
  const map = new Map(currentTasksDetailed.value.map((task) => [task.id, task]))
  return map
})

const enrichedTasks = computed(() =>
  tasks.value.map((task) => detailedById.value.get(task.id) || task),
)

function stageOf(task: LocalizationTask): PipelineStage {
  return pipelineStage(detailedById.value.get(task.id) || task)
}

const stageCounts = computed(() => {
  const counts: Record<PipelineStage, number> = {
    requesting: 0,
    extracting: 0,
    transcribing: 0,
    translating: 0,
    verifying: 0,
    dubbing: 0,
    dub_blocked: 0,
    failed: 0,
    other: 0,
  }
  for (const task of enrichedTasks.value) {
    const cap = (task.capability || 'subtitles').toLowerCase()
    if (task.status === 'failed' && isUnresolvedFailedTask(task, enrichedTasks.value)) {
      counts.failed += 1
      continue
    }
    if (cap === 'audio' && task.status === 'blocked') {
      counts.dub_blocked += 1
      continue
    }
    const stage = stageOf(task)
    counts[stage] += 1
  }
  return counts
})

type PipelineCard = {
  key: string
  label: string
  count: number
  to: string
  accent: string
  always?: boolean
}

const subtitleCards = computed<PipelineCard[]>(() => {
  const c = stageCounts.value
  return [
    {
      key: 'missing',
      label: 'Missing',
      count: pipelineLoaded.value ? candidateHealth.value.missing : 0,
      to: '/media?filter=needs-work',
      accent: 'border-l-accent',
      always: true,
    },
    {
      key: 'ready',
      label: 'Ready',
      count: pipelineLoaded.value ? candidateHealth.value.ready : 0,
      to: '/media?filter=needs-work',
      accent: 'border-l-sky-500',
      always: true,
    },
    {
      key: 'requesting',
      label: 'Requesting',
      count: c.requesting,
      to: '/media?filter=in-progress',
      accent: 'border-l-amber-500',
    },
    {
      key: 'extracting',
      label: 'Extracting',
      count: c.extracting,
      to: '/media?filter=in-progress',
      accent: 'border-l-amber-500',
    },
    {
      key: 'transcribing',
      label: 'Transcribing',
      count: c.transcribing,
      to: '/media?filter=in-progress',
      accent: 'border-l-amber-500',
    },
    {
      key: 'translating',
      label: 'Translating',
      count: c.translating,
      to: '/media?filter=in-progress',
      accent: 'border-l-amber-500',
    },
    {
      key: 'verifying',
      label: 'Verifying',
      count: c.verifying,
      to: '/media?filter=in-progress',
      accent: 'border-l-amber-500',
    },
    {
      key: 'failed',
      label: 'Failed',
      count: c.failed,
      to: '/media?filter=failed',
      accent: 'border-l-red-500',
      always: true,
    },
  ].filter((card) => card.always || card.count > 0)
})

const audioCards = computed<PipelineCard[]>(() => {
  const c = stageCounts.value
  return [
    {
      key: 'dubbing',
      label: 'Dubbing',
      count: c.dubbing,
      to: '/media?filter=in-progress',
      accent: 'border-l-amber-500',
    },
    {
      key: 'dub_blocked',
      label: 'Dub blocked',
      count: c.dub_blocked,
      to: '/media?filter=failed',
      accent: 'border-l-amber-600',
    },
  ].filter((card) => card.count > 0)
})

const liveTasks = computed(() =>
  currentTasksDetailed.value
    .filter((task) => task.executions?.some((job) => isOpenJobStatus(job.status)))
    .slice(0, LIVE_LIMIT),
)

const liveOverflow = computed(() => {
  const active = currentTasksDetailed.value.filter((task) =>
    task.executions?.some((job) => isOpenJobStatus(job.status)),
  ).length
  return Math.max(0, active - LIVE_LIMIT)
})

const failedTasks = computed(() =>
  enrichedTasks.value.filter((t) => isUnresolvedFailedTask(t, enrichedTasks.value)).slice(0, 8),
)

const dubBlockedTasks = computed(() =>
  enrichedTasks.value
    .filter(
      (t) =>
        (t.capability || 'subtitles') === 'audio' &&
        t.status === 'blocked' &&
        (t.substate === 'awaiting_subtitles' || t.error_code === 'subtitle_missing'),
    )
    .slice(0, 8),
)

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

const budgetPct = computed(() => aiOverview.value?.budget.percent_used)
const budgetHot = computed(
  () => Boolean(aiOverview.value?.budget.enabled && (budgetPct.value ?? 0) >= 90),
)

type Intervention = {
  key: string
  message?: string
  mediaTitle?: string
  mediaTo?: string
  messagePrefix?: string
  messageSuffix?: string
  to?: string
  actionLabel?: string
  onAction?: () => void
}

const interventions = computed<Intervention[]>(() => {
  const items: Intervention[] = []
  if (pipelineError.value) {
    items.push({ key: 'pipeline', message: pipelineError.value })
  }
  if (!bazarrOk.value) {
    items.push({
      key: 'bazarr',
      message:
        health.value?.bazarr === 'unreachable' ? 'Bazarr is unreachable.' : 'Bazarr is not configured.',
      to: '/settings/providers',
      actionLabel: 'Open settings',
    })
  }
  if (!openRouterOk.value) {
    items.push({
      key: 'openrouter',
      message:
        health.value?.openrouter === 'unreachable'
          ? 'Cannot connect to the AI service.'
          : 'OpenRouter is not configured.',
      to: '/settings/providers',
      actionLabel: 'Open settings',
    })
  }
  if (health.value?.planner_error) {
    items.push({
      key: 'planner',
      message: 'Localization planner failed to resume after restart.',
    })
  }
  if (budgetHot.value) {
    items.push({
      key: 'budget',
      message: `Monthly budget ${(budgetPct.value || 0).toFixed(0)}% used.`,
      to: '/settings/models',
      actionLabel: 'Configure models',
    })
  }
  for (const task of failedTasks.value) {
    items.push({
      key: `fail-${task.id}`,
      mediaTitle: localizationTaskTitle(task),
      mediaTo: mediaHref(task.media_item_id),
      messageSuffix: task.error_message ? ` — ${task.error_message}` : '',
      to: mediaHref(task.media_item_id),
      actionLabel: canRetryTask(task.status) ? 'Retry' : 'Open',
      onAction: canRetryTask(task.status) ? () => retryTask(task.id) : undefined,
    })
  }
  for (const task of dubBlockedTasks.value) {
    items.push({
      key: `dub-block-${task.id}`,
      mediaTitle: localizationTaskTitle(task),
      mediaTo: mediaHref(task.media_item_id),
      messagePrefix: 'Dub blocked (need subtitles): ',
      to: mediaHref(task.media_item_id),
      actionLabel: 'Localize subtitles',
    })
  }
  return items
})

const periodCard = computed(() => {
  const cards = aiOverview.value?.cards
  if (!cards) return null
  if (moneyPeriod.value === 'today') return cards.today || null
  if (moneyPeriod.value === '7d') return cards['7d'] || cards.week || null
  return cards.month || null
})

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

function formatTokens(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function completedJobTitle(job: Job): string {
  return job.media_title || job.media_path || `Job #${job.id}`
}

async function loadPipeline(refresh = false) {
  pipelineLoading.value = true
  pipelineError.value = null
  try {
    if (refresh) await store.loadCandidates()
    else await store.loadCandidatesCached()
    pipelineLoaded.value = true
  } catch (err) {
    pipelineError.value = err instanceof Error ? err.message : String(err)
  } finally {
    pipelineLoading.value = false
  }
}

async function loadTasks() {
  try {
    tasks.value = await api.getLocalizationTasks({ limit: 200 })
  } catch {
    /* keep previous */
  }
}

async function loadCurrentLocalization() {
  try {
    currentTasksDetailed.value = await api.getLocalizationTasks({
      active_only: true,
      include_detail: true,
      limit: LIVE_LIMIT,
    })
  } catch {
    /* keep previous */
  }
}

async function loadCompletedJobs() {
  try {
    completedJobs.value = await api.getJobs({
      status: 'completed',
      sort: 'completed_at',
      limit: 2,
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
    aiOverview.value = await api.getAiOverview(moneyPeriod.value === '7d' ? '7d' : moneyPeriod.value)
  } catch {
    aiOverview.value = null
  }
}

async function refreshDashboard() {
  await Promise.all([
    loadPipeline(true),
    loadTasks(),
    loadCurrentLocalization(),
    loadCompletedJobs(),
    loadHealth(),
    loadAutomation(),
    loadAiSummary(),
  ])
}

async function runScan() {
  scanning.value = true
  actionError.value = null
  try {
    const result = await api.runAutomationScan()
    await loadAutomation()
    if (!result.ok) {
      actionError.value = result.message || 'Automatic scan did not run.'
      return
    }
    await Promise.all([loadTasks(), loadPipeline(true)])
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    scanning.value = false
  }
}

async function retryTask(id: number) {
  if (actionBusyId.value != null) return
  actionBusyId.value = id
  actionError.value = null
  try {
    await api.retryLocalizationTask(id)
    await Promise.all([loadTasks(), loadCurrentLocalization()])
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    actionBusyId.value = null
  }
}


async function cancelLiveTask(taskId: number) {
  actionError.value = null
  try {
    await api.cancelLocalizationTask(taskId)
    await Promise.all([loadTasks(), loadCurrentLocalization()])
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  }
}

watch(moneyPeriod, () => {
  loadAiSummary().catch(() => undefined)
})

onMounted(async () => {
  await store.loadSettings().catch(() => undefined)
  await Promise.all([
    loadHealth(),
    loadAutomation(),
    loadAiSummary(),
    loadPipeline(false),
    loadTasks(),
    loadCurrentLocalization(),
    loadCompletedJobs(),
  ])
  timer = window.setInterval(() => {
    loadTasks().catch(() => undefined)
    loadCurrentLocalization().catch(() => undefined)
    loadCompletedJobs().catch(() => undefined)
    loadHealth().catch(() => undefined)
    loadAutomation().catch(() => undefined)
  }, 30000)
  stopLive = onLiveEvent((event) => {
    if (event.type === 'hello') return
    loadTasks().catch(() => undefined)
    loadCurrentLocalization().catch(() => undefined)
    loadCompletedJobs().catch(() => undefined)
  })
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  stopLive?.()
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="font-display text-2xl font-bold sm:text-3xl">Dashboard</h1>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          :disabled="pipelineLoading || store.loading"
          @click="refreshDashboard"
        >
          {{ pipelineLoading || store.loading ? 'Refreshing…' : 'Refresh' }}
        </button>
        <button
          type="button"
          class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90"
          @click="modalOpen = true"
        >
          Request subtitles
        </button>
        <button
          type="button"
          class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90"
          @click="dubModalOpen = true"
        >
          Request dubbing
        </button>
      </div>
    </div>

    <OperatorChatBar />

    <nav class="flex gap-1 border-b border-ink-200 dark:border-ink-800">
      <button
        type="button"
        class="rounded-t-md px-3 py-2 text-sm font-medium"
        :class="
          activeTab === 'ops'
            ? 'bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white'
            : 'text-ink-600 hover:bg-ink-50 dark:text-ink-300 dark:hover:bg-ink-900'
        "
        @click="setTab('ops')"
      >
        Ops
      </button>
      <button
        type="button"
        class="rounded-t-md px-3 py-2 text-sm font-medium"
        :class="
          activeTab === 'ai'
            ? 'bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white'
            : 'text-ink-600 hover:bg-ink-50 dark:text-ink-300 dark:hover:bg-ink-900'
        "
        @click="setTab('ai')"
      >
        AI
      </button>
    </nav>

    <p
      v-if="actionError"
      class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
    >
      {{ actionError }}
    </p>

    <template v-if="activeTab === 'ops'">
      <section
        v-if="interventions.length"
        class="rounded-md border border-l-4 border-ink-200 border-l-red-500 bg-white p-4 dark:border-ink-800 dark:bg-ink-900"
      >
        <h2 class="text-xs font-semibold uppercase tracking-wide text-ink-500">Needs attention</h2>
        <ul class="mt-3 divide-y divide-ink-100 dark:divide-ink-800">
          <li
            v-for="item in interventions"
            :key="item.key"
            class="flex flex-wrap items-start justify-between gap-2 py-2.5 first:pt-0 last:pb-0"
          >
            <p class="min-w-0 flex-1 text-sm text-ink-800 dark:text-ink-100">
              <template v-if="item.mediaTitle && item.mediaTo">
                {{ item.messagePrefix }}
                <RouterLink
                  class="font-medium text-accent hover:underline"
                  :to="item.mediaTo"
                >
                  {{ item.mediaTitle }}
                </RouterLink>
                {{ item.messageSuffix }}
              </template>
              <template v-else>{{ item.message }}</template>
            </p>
            <div class="flex shrink-0 gap-2">
              <button
                v-if="item.onAction"
                type="button"
                class="rounded-md border border-ink-300 px-2.5 py-1 text-xs font-semibold dark:border-ink-600"
                :disabled="actionBusyId != null"
                @click="item.onAction()"
              >
                {{ item.actionLabel || 'Act' }}
              </button>
              <RouterLink
                v-else-if="item.to"
                class="rounded-md border border-ink-300 px-2.5 py-1 text-xs font-semibold dark:border-ink-600"
                :to="item.to"
              >
                {{ item.actionLabel || 'Open' }}
              </RouterLink>
            </div>
          </li>
        </ul>
      </section>

      <div>
        <h2 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">Subtitles</h2>
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <RouterLink
            v-for="card in subtitleCards"
            :key="card.key"
            :to="card.to"
            class="rounded-md border border-l-4 border-ink-200 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900"
            :class="card.accent"
          >
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">{{ card.label }}</div>
            <div class="mt-1 font-mono text-2xl font-semibold">
              {{ pipelineLoaded || !['missing', 'ready'].includes(card.key) ? card.count : '—' }}
            </div>
          </RouterLink>
        </div>
      </div>

      <div v-if="audioCards.length">
        <h2 class="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">Audio</h2>
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <RouterLink
            v-for="card in audioCards"
            :key="card.key"
            :to="card.to"
            class="rounded-md border border-l-4 border-ink-200 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900"
            :class="card.accent"
          >
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">{{ card.label }}</div>
            <div class="mt-1 font-mono text-2xl font-semibold">{{ card.count }}</div>
          </RouterLink>
        </div>
      </div>

      <section class="space-y-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-xs font-semibold uppercase tracking-wide text-ink-500">Cost & quality</h2>
          <div class="flex flex-wrap gap-1">
            <button
              v-for="opt in [
                { id: 'today' as const, label: 'Today' },
                { id: '7d' as const, label: '7 days' },
                { id: 'month' as const, label: 'Month' },
              ]"
              :key="opt.id"
              type="button"
              class="rounded-md px-2.5 py-1 text-xs font-medium"
              :class="
                moneyPeriod === opt.id
                  ? 'bg-ink-100 font-semibold dark:bg-ink-800'
                  : 'text-ink-600 hover:bg-ink-50 dark:text-ink-300'
              "
              @click="moneyPeriod = opt.id"
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          <div class="rounded-md border border-l-4 border-ink-200 border-l-violet-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Spend</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              {{ formatUsd(periodCard?.cost_usd ?? aiOverview?.ai_summary?.this_month_cost_usd) }}
            </div>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-violet-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Translation cost</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              {{ formatUsd(periodCard?.translation_cost_usd) }}
            </div>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-amber-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Repair cost</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              {{ formatUsd(periodCard?.repair_cost_usd) }}
            </div>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-violet-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Budget left</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              <template v-if="aiOverview?.budget.enabled">
                {{ formatUsd(aiOverview.budget.remaining) }}
              </template>
              <template v-else>Off</template>
            </div>
            <p v-if="aiOverview?.budget.enabled" class="mt-1 text-xs text-ink-500">
              {{ (budgetPct || 0).toFixed(1) }}% used
            </p>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-sky-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Requests</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              {{ periodCard?.requests ?? aiOverview?.requests ?? '—' }}
            </div>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-sky-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Tokens</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              {{ formatTokens(aiOverview?.tokens?.total) }}
            </div>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-emerald-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Clean success</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              {{ formatPct(aiOverview?.clean_success_rate ?? aiOverview?.ai_summary?.clean_success_rate) }}
            </div>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-amber-500 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Repair rate</div>
            <div class="mt-1 font-mono text-xl font-semibold">
              {{ formatPct(aiOverview?.repair_rate) }}
            </div>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-ink-400 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Free / paid</div>
            <div class="mt-1 font-mono text-sm font-semibold">
              {{ aiOverview?.free_requests ?? 0 }} / {{ aiOverview?.paid_requests ?? 0 }}
            </div>
            <p class="mt-1 text-xs text-ink-500">
              {{ formatUsd(aiOverview?.paid_cost_usd) }} paid
            </p>
          </div>
          <div class="rounded-md border border-l-4 border-ink-200 border-l-ink-400 bg-white px-3 py-3 dark:border-ink-800 dark:bg-ink-900">
            <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Best model</div>
            <div
              class="mt-1 truncate font-mono text-sm font-semibold"
              :title="aiOverview?.ai_summary?.best_model_id || ''"
            >
              {{ aiOverview?.ai_summary?.best_model_id || '—' }}
            </div>
          </div>
        </div>
        <template v-if="aiOverview?.budget.enabled">
          <div class="h-2 overflow-hidden rounded-md bg-ink-100 dark:bg-ink-800">
            <div
              class="h-full bg-accent"
              :style="{ width: `${Math.min(100, budgetPct || 0)}%` }"
            />
          </div>
          <p class="text-xs text-ink-500">
            {{ formatUsd(aiOverview.budget.used) }} / {{ formatUsd(aiOverview.budget.limit) }}
          </p>
        </template>
      </section>

      <div v-if="liveTasks.length">
        <div class="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h2 class="text-xs font-semibold uppercase tracking-wide text-ink-500">Live work</h2>
          <RouterLink
            v-if="liveOverflow > 0"
            class="text-xs font-medium text-accent hover:underline"
            to="/media?filter=in-progress"
          >
            +{{ liveOverflow }} more on Media
          </RouterLink>
        </div>
        <RunningJobsPanel
          :tasks="liveTasks"
          show-title
          @cancel="cancelLiveTask"
          @refreshed="
            () => {
              loadTasks()
              loadCurrentLocalization()
            }
          "
        />
      </div>

      <section class="rounded-md border border-l-4 border-ink-200 border-l-emerald-500 bg-white p-4 dark:border-ink-800 dark:bg-ink-900">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">Last 2 completed jobs</h2>
          <RouterLink class="text-xs font-medium text-accent hover:underline" to="/media?filter=completed">
            View completed media
          </RouterLink>
        </div>
        <p v-if="!completedJobs.length" class="mt-4 text-sm text-ink-500">No completed jobs yet.</p>
        <ul v-else class="mt-4 divide-y divide-ink-100 dark:divide-ink-800">
          <li v-for="job in completedJobs" :key="job.id" class="py-3 first:pt-0 last:pb-0">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <RouterLink
                  class="block truncate font-medium text-accent hover:underline"
                  :to="`/jobs/${job.id}`"
                  :title="completedJobTitle(job)"
                >
                  {{ completedJobTitle(job) }}
                </RouterLink>
                <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
                  {{ jobKindLabel(job.job_kind) }} #{{ job.id }}
                  <span v-if="job.target_language" class="text-ink-500"> · {{ job.target_language }}</span>
                  <span v-if="job.model" class="text-ink-500"> · {{ job.model }}</span>
                </p>
              </div>
              <time class="shrink-0 text-xs text-ink-500" :datetime="job.completed_at || undefined">
                {{ formatDateTime(job.completed_at) }}
              </time>
            </div>
          </li>
        </ul>
      </section>

      <section class="rounded-md border border-l-4 border-ink-200 border-l-ink-400 bg-white p-4 dark:border-ink-800 dark:bg-ink-900">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="font-display text-lg font-semibold">Automation</h2>
            <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
              {{ store.settings?.automatic_fallback_enabled ? 'Enabled' : 'Disabled' }}
              · last scan
              {{ automation?.last_scan_at ? formatDateTime(automation.last_scan_at) : 'never' }}
              <template v-if="automation?.next_scan_at">
                · next {{ formatDateTime(automation.next_scan_at) }}
              </template>
            </p>
            <p v-if="automation?.last_result" class="mt-1 text-xs text-ink-500">
              Last result:
              {{ automation.last_result.ok === false ? 'failed' : 'ok' }}
              <template v-if="automation.last_result.created_count != null">
                · {{ automation.last_result.created_count }} created
              </template>
              <template v-if="automation.last_result.message">
                · {{ automation.last_result.message }}
              </template>
            </p>
          </div>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            :disabled="scanning || !store.settings?.automatic_fallback_enabled"
            @click="runScan"
          >
            {{ scanning ? 'Scanning…' : 'Scan now' }}
          </button>
        </div>
      </section>
    </template>

    <template v-else>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap gap-1">
          <button
            type="button"
            class="rounded-md px-3 py-1.5 text-sm"
            :class="
              aiReport === 'overview'
                ? 'bg-ink-100 font-semibold dark:bg-ink-800'
                : 'text-ink-600 hover:bg-ink-50 dark:text-ink-300'
            "
            @click="setAiReport('overview')"
          >
            Overview
          </button>
          <button
            type="button"
            class="rounded-md px-3 py-1.5 text-sm"
            :class="
              aiReport === 'usage'
                ? 'bg-ink-100 font-semibold dark:bg-ink-800'
                : 'text-ink-600 hover:bg-ink-50 dark:text-ink-300'
            "
            @click="setAiReport('usage')"
          >
            Usage
          </button>
        </div>
        <RouterLink
          class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
          to="/settings/models"
        >
          Configure models
        </RouterLink>
      </div>
      <AiOverviewView v-if="aiReport === 'overview'" embedded />
      <AiUsageView v-else embedded />
    </template>

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
    <RequestDubModal
      :open="dubModalOpen"
      @close="dubModalOpen = false"
      @created="
        () => {
          loadTasks()
          loadCurrentLocalization()
        }
      "
    />
  </section>
</template>
