<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type {
  AiOverview,
  AutomationStatus,
  Candidate,
  GlossarySummary,
  Health,
  LocalizationTask,
} from '../types'
import { formatDateTime } from '../utils/datetime'
import { mediaHref } from '../utils/mediaNav'
import { isActiveTaskStatus, taskStatusIcon, taskStatusLabel } from '../utils/status'

const store = useAppStore()
const health = ref<Health | null>(null)
const automation = ref<AutomationStatus | null>(null)
const aiOverview = ref<AiOverview | null>(null)
const glossary = ref<GlossarySummary | null>(null)
const tasks = ref<LocalizationTask[]>([])
const pipelineLoading = ref(false)
const pipelineError = ref<string | null>(null)
const pipelineLoaded = ref(false)
const modalOpen = ref(false)
let timer: number | undefined

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

const currentLocalization = computed(() =>
  tasks.value.filter((t) => isActiveTaskStatus(t.status)).slice(0, LIST_LIMIT),
)

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
  if (health.value) return health.value.bazarr === 'configured'
  return Boolean(store.settings?.bazarr_url && store.settings.bazarr_api_key_configured)
})

const openRouterOk = computed(() => {
  if (health.value) return health.value.openrouter === 'configured'
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
  if (!bazarrOk.value) reasons.push('Bazarr is not configured.')
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
  const pending = glossary.value?.awaiting_review || 0
  if (pending > 0) {
    reasons.push(`${pending} glossary term${pending === 1 ? '' : 's'} awaiting review.`)
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
    loadHealth(),
    loadAutomation(),
    loadAiSummary(),
    loadGlossary(),
  ])
}

async function loadTasks() {
  try {
    tasks.value = await api.getLocalizationTasks({ limit: 100 })
  } catch {
    /* keep previous */
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

async function loadGlossary() {
  try {
    const lang = store.settings?.target_language.code
    glossary.value = await api.getGlossarySummary(lang)
  } catch {
    glossary.value = null
  }
}

onMounted(async () => {
  await store.loadSettings().catch(() => undefined)
  await Promise.all([
    store.loadJobs().catch(() => undefined),
    loadHealth(),
    loadAutomation(),
    loadAiSummary(),
    loadGlossary(),
    loadPipeline(false),
    loadTasks(),
  ])
  timer = window.setInterval(() => {
    loadTasks().catch(() => undefined)
  }, 4000)
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
      </div>
    </div>

    <section
      class="rounded-xl border px-5 py-4"
      :class="{
        'border-emerald-300 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/30':
          heroTone === 'healthy',
        'border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60': heroTone === 'idle',
        'border-amber-300 bg-amber-50/80 dark:border-amber-900 dark:bg-amber-950/30':
          heroTone === 'attention',
      }"
    >
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div class="text-xs uppercase tracking-wide text-ink-500">Status</div>
          <div class="mt-1 font-display text-2xl font-bold">
            <span v-if="heroTone === 'attention'">⚠ Attention needed</span>
            <span v-else-if="heroTone === 'idle'">● Idle</span>
            <span v-else>● Healthy</span>
          </div>
          <ul v-if="attentionReasons.length" class="mt-2 space-y-1 text-sm text-ink-700 dark:text-ink-200">
            <li v-for="reason in attentionReasons" :key="reason">{{ reason }}</li>
          </ul>
          <p v-else class="mt-2 text-sm text-ink-600 dark:text-ink-300">
            Automation
            {{ store.settings?.automatic_fallback_enabled ? 'on' : 'off' }}
            · last scan
            {{ automation?.last_scan_at ? formatDateTime(automation.last_scan_at) : 'never' }}
          </p>
        </div>
        <dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt class="text-xs uppercase text-ink-500">Automation</dt>
            <dd class="font-semibold">
              {{ store.settings?.automatic_fallback_enabled ? 'Enabled' : 'Disabled' }}
            </dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Active tasks</dt>
            <dd class="font-semibold">{{ currentLocalization.length }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Completed today</dt>
            <dd class="font-semibold">{{ completedToday }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">AI jobs</dt>
            <dd class="font-semibold">{{ aiOverview?.active_jobs ?? 0 }}</dd>
          </div>
        </dl>
      </div>
    </section>

    <div class="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
      <RouterLink
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60"
        to="/media?filter=needs-work"
      >
        <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Missing subtitles</div>
        <div class="mt-1 font-display text-2xl font-bold">
          {{ pipelineLoaded ? candidateHealth.missing : '—' }}
        </div>
      </RouterLink>
      <RouterLink
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60"
        to="/media?filter=needs-work"
      >
        <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Ready to translate</div>
        <div class="mt-1 font-display text-2xl font-bold">
          {{ pipelineLoaded ? candidateHealth.ready : '—' }}
        </div>
      </RouterLink>
      <RouterLink
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60"
        to="/ai/overview"
      >
        <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">This month</div>
        <div class="mt-1 font-display text-2xl font-bold">{{ formatUsd(monthCost) }}</div>
      </RouterLink>
      <RouterLink
        class="rounded-xl border px-4 py-3 hover:border-accent/50"
        :class="
          (glossary?.awaiting_review || 0) > 0
            ? 'border-amber-300 bg-amber-50/80 dark:border-amber-900 dark:bg-amber-950/30'
            : 'border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60'
        "
        to="/settings/glossary?tab=review"
      >
        <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">Glossary review</div>
        <div class="mt-1 font-display text-2xl font-bold">{{ glossary?.awaiting_review ?? '—' }}</div>
      </RouterLink>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">AI</h2>
          <RouterLink class="text-sm font-semibold text-accent hover:underline" to="/ai/overview">
            Open AI dashboard
          </RouterLink>
        </div>
        <dl class="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt class="text-xs uppercase text-ink-500">This month</dt>
            <dd class="font-display text-xl font-bold">{{ formatUsd(monthCost) }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Today</dt>
            <dd class="font-display text-xl font-bold">
              {{ formatUsd(aiOverview?.cards?.today?.cost_usd) }}
            </dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Requests</dt>
            <dd class="font-display text-xl font-bold">{{ monthRequests ?? '—' }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Clean success</dt>
            <dd class="font-display text-xl font-bold">{{ formatPct(cleanSuccess) }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Best observed</dt>
            <dd class="truncate font-semibold" :title="bestModel || ''">{{ bestModel || '—' }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Budget</dt>
            <dd class="font-semibold">
              <template v-if="aiOverview?.budget.enabled">
                {{ (budgetPct || 0).toFixed(1) }}% used
              </template>
              <template v-else>Off</template>
            </dd>
          </div>
        </dl>
        <template v-if="aiOverview?.budget.enabled">
          <div class="mt-4 h-2.5 overflow-hidden rounded bg-ink-100 dark:bg-ink-800">
            <div
              class="h-full bg-accent"
              :style="{ width: `${Math.min(100, budgetPct || 0)}%` }"
            />
          </div>
          <p class="mt-2 text-xs text-ink-500">
            {{ formatUsd(aiOverview.budget.used) }} / {{ formatUsd(aiOverview.budget.limit) }}
            · {{ formatUsd(aiOverview.budget.remaining) }} remaining
          </p>
        </template>
      </section>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">Glossary</h2>
          <RouterLink class="text-sm font-semibold text-accent hover:underline" to="/settings/glossary">
            Manage glossary
          </RouterLink>
        </div>
        <dl class="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt class="text-xs uppercase text-ink-500">Awaiting review</dt>
            <dd class="font-display text-xl font-bold">{{ glossary?.awaiting_review ?? '—' }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Active terms</dt>
            <dd class="font-display text-xl font-bold">{{ glossary?.active_terms ?? '—' }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Locked</dt>
            <dd class="font-display text-xl font-bold">{{ glossary?.locked_terms ?? '—' }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Scopes</dt>
            <dd class="font-display text-xl font-bold">{{ glossary?.scopes ?? '—' }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Universes</dt>
            <dd class="font-semibold">{{ glossary?.universes ?? '—' }}</dd>
          </div>
          <div>
            <dt class="text-xs uppercase text-ink-500">Series / movies</dt>
            <dd class="font-semibold">
              {{ glossary ? `${glossary.series} / ${glossary.movies}` : '—' }}
            </dd>
          </div>
        </dl>
        <ul v-if="glossary?.pending_scopes?.length" class="mt-4 space-y-2 text-sm">
          <li v-for="scope in glossary.pending_scopes" :key="scope.id">
            <RouterLink
              class="font-medium text-accent hover:underline"
              :to="`/settings/glossary?tab=review&scope=${scope.id}`"
            >
              {{ scope.display_name }}
            </RouterLink>
            <span class="text-ink-500"> · {{ scope.suggested_count }} to review</span>
          </li>
        </ul>
        <p v-else class="mt-4 text-sm text-ink-500">No suggested terms awaiting review.</p>
      </section>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <div class="space-y-3">
        <div class="flex items-baseline justify-between gap-2">
          <h2 class="font-display text-lg font-semibold">Current localization</h2>
          <RouterLink class="text-xs font-medium text-accent hover:underline" to="/media">
            View all
          </RouterLink>
        </div>
        <div class="rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
          <p v-if="!currentLocalization.length" class="px-4 py-8 text-center text-sm text-ink-500">
            No current localization.
          </p>
          <ul v-else class="divide-y divide-ink-100 dark:divide-ink-800">
            <li v-for="task in currentLocalization" :key="`cur-${task.id}`" class="px-4 py-3">
              <RouterLink class="font-medium text-accent hover:underline" :to="mediaHref(task.media_item_id)">
                {{ task.media_title || `Media #${task.media_item_id}` }}
              </RouterLink>
              <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
                <span>{{ task.target_language_name }}</span>
                <span>{{ taskStatusIcon(task.status) }} {{ taskStatusLabel(task.status, task.substate) }}</span>
                <span class="capitalize">{{ task.origin }}</span>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <div class="space-y-3">
        <h2 class="font-display text-lg font-semibold">Needs attention</h2>
        <p
          v-if="pipelineError"
          class="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800"
        >
          {{ pipelineError }}
        </p>
        <div class="rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
          <p v-if="!failedTasks.length && bazarrOk && openRouterOk" class="px-4 py-8 text-center text-sm text-ink-500">
            Nothing needs attention.
          </p>
          <ul v-else class="divide-y divide-ink-100 dark:divide-ink-800">
            <li v-if="!bazarrOk" class="px-4 py-3 text-sm">
              Bazarr is not configured.
              <RouterLink class="ml-1 font-medium text-accent hover:underline" to="/settings/providers">
                Open settings
              </RouterLink>
            </li>
            <li v-if="!openRouterOk" class="px-4 py-3 text-sm">
              OpenRouter is not configured.
              <RouterLink class="ml-1 font-medium text-accent hover:underline" to="/settings/providers">
                Open settings
              </RouterLink>
            </li>
            <li v-for="task in failedTasks" :key="`fail-${task.id}`" class="px-4 py-3">
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
        </div>
      </div>
    </div>

    <RequestSubtitlesModal :open="modalOpen" @close="modalOpen = false" @created="loadTasks" />
  </section>
</template>
