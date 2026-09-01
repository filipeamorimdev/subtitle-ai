<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../../services/api'
import type { AiCosts, AiModelJobTimes, AiOverview } from '../../types'
import { formatDateTime, formatDuration } from '../../utils/datetime'

withDefaults(
  defineProps<{
    embedded?: boolean
  }>(),
  { embedded: false },
)

const period = ref('month')
const overview = ref<AiOverview | null>(null)
const costs = ref<AiCosts | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)
const hoverPoint = ref<{ date: string; cost_usd: number; request_count?: number } | null>(null)
const jobTimes = ref<AiModelJobTimes | null>(null)
const jobTimesError = ref<string | null>(null)
const jobTimesLoading = ref(false)

type RankingRow = AiOverview['ranking'][number]
type RankingSortKey =
  | 'configured_priority'
  | 'adaptive_rank'
  | 'provider'
  | 'model_id'
  | 'clean_success_rate'
  | 'average_cost_per_clean_success_usd'
  | 'average_latency_ms'
  | 'average_job_duration_seconds'
  | 'sample_count'

const rankingSort = ref<{ key: RankingSortKey; direction: 'asc' | 'desc' } | null>(null)

const rankingColumns: Array<{ key: RankingSortKey; label: string; title?: string }> = [
  { key: 'configured_priority', label: 'Priority' },
  { key: 'adaptive_rank', label: 'Adaptive' },
  { key: 'provider', label: 'Provider' },
  { key: 'model_id', label: 'Model' },
  { key: 'clean_success_rate', label: 'Clean' },
  { key: 'average_cost_per_clean_success_usd', label: 'Cost' },
  { key: 'average_latency_ms', label: 'Speed' },
  { key: 'average_job_duration_seconds', label: 'Mean time', title: 'Wall-clock time of successful finished translation jobs' },
  { key: 'sample_count', label: 'Samples' },
]

function rankingSortValue(row: RankingRow, key: RankingSortKey): string | number | null | undefined {
  if (key === 'provider') return row.provider_name || row.provider_id || 'OpenRouter'
  return row[key]
}

function sortRanking(key: RankingSortKey) {
  rankingSort.value = rankingSort.value?.key === key
    ? { key, direction: rankingSort.value.direction === 'asc' ? 'desc' : 'asc' }
    : { key, direction: 'asc' }
}

function rankingSortLabel(key: RankingSortKey): string {
  if (rankingSort.value?.key !== key) return ''
  return rankingSort.value.direction === 'asc' ? ' (ascending)' : ' (descending)'
}

const sortedRanking = computed(() => {
  const rows = overview.value?.ranking ?? []
  if (!rankingSort.value) return rows

  const { key, direction } = rankingSort.value
  const multiplier = direction === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const aValue = rankingSortValue(a, key)
    const bValue = rankingSortValue(b, key)
    if (aValue == null) return bValue == null ? 0 : 1
    if (bValue == null) return -1
    if (typeof aValue === 'number' && typeof bValue === 'number') return (aValue - bValue) * multiplier
    return String(aValue).localeCompare(String(bValue), undefined, { numeric: true, sensitivity: 'base' }) * multiplier
  })
})

function formatUsd(n: number | null | undefined, digits = 2): string {
  if (n == null) return '—'
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n >= 0.01) return `$${n.toFixed(3)}`
  if (n === 0) return '$0'
  return `$${n.toFixed(digits)}`
}

function formatPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}

const budgetTone = computed(() => {
  const pct = overview.value?.budget.percent_used
  if (pct == null) return 'neutral'
  if (pct >= 100) return 'blocked'
  if (pct >= 90) return 'danger'
  if (pct >= 70) return 'warn'
  return 'ok'
})

const statusTone = computed(() => {
  const s = overview.value?.status
  if (s === 'attention') return 'attention'
  if (s === 'idle') return 'idle'
  return 'healthy'
})

const maxCost = computed(() => Math.max(0.0001, ...(costs.value?.series.map((s) => s.cost_usd) || [0])))

function costPeriodFor(p: string): string {
  if (p === 'month') return 'month'
  if (p === 'today') return 'today'
  if (p === 'all') return 'all'
  return p
}

async function load() {
  loading.value = true
  error.value = null
  hoverPoint.value = null
  closeJobTimes()
  try {
    overview.value = await api.getAiOverview(period.value)
    costs.value = await api.getAiCosts(costPeriodFor(period.value))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function closeJobTimes() {
  jobTimes.value = null
  jobTimesError.value = null
  jobTimesLoading.value = false
}

async function openJobTimes(row: AiOverview['ranking'][number]) {
  if (!row.completed_job_count) return
  jobTimesLoading.value = true
  jobTimesError.value = null
  jobTimes.value = {
    period: period.value,
    provider_id: row.provider_id || 'openrouter',
    model_id: row.model_id,
    average_job_duration_seconds: row.average_job_duration_seconds ?? null,
    completed_job_count: row.completed_job_count,
    items: [],
  }
  try {
    jobTimes.value = await api.getAiModelJobTimes({
      period: period.value,
      provider_id: row.provider_id,
      model_id: row.model_id,
    })
  } catch (err) {
    jobTimesError.value = err instanceof Error ? err.message : String(err)
  } finally {
    jobTimesLoading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex flex-wrap gap-1">
        <button
          v-for="opt in [
            { id: 'today', label: 'Today' },
            { id: '7d', label: '7 days' },
            { id: '30d', label: '30 days' },
            { id: 'month', label: 'This month' },
            { id: 'all', label: 'All time' },
          ]"
          :key="opt.id"
          type="button"
          class="rounded-md px-3 py-1.5 text-sm"
          :class="period === opt.id ? 'bg-ink-100 font-semibold dark:bg-ink-800' : 'text-ink-600 hover:bg-ink-50 dark:text-ink-300'"
          @click="period = opt.id; load()"
        >
          {{ opt.label }}
        </button>
      </div>
    </div>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">{{ error }}</p>
    <p v-else-if="loading" class="text-ink-500">Loading AI overview…</p>

    <template v-else-if="overview">
      <section
        v-if="!embedded"
        class="rounded-md border px-5 py-4"
        :class="{
          'border-emerald-300 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/30': statusTone === 'healthy',
          'border-ink-200 bg-white dark:border-ink-800 dark:bg-ink-900': statusTone === 'idle',
          'border-amber-300 bg-amber-50/80 dark:border-amber-900 dark:bg-amber-950/30': statusTone === 'attention',
        }"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div class="text-xs uppercase tracking-wide text-ink-500">AI status</div>
            <div class="mt-1 font-display text-xl font-bold">
              <span v-if="overview.status === 'attention'">Attention needed</span>
              <span v-else-if="overview.status === 'idle'">Idle</span>
              <span v-else>Healthy</span>
            </div>
            <ul v-if="overview.status_reasons?.length" class="mt-2 space-y-1 text-sm text-ink-700 dark:text-ink-200">
              <li v-for="reason in overview.status_reasons" :key="reason">{{ reason }}</li>
            </ul>
          </div>
          <div class="grid grid-cols-3 gap-4 text-sm">
            <div>
              <div class="text-xs uppercase text-ink-500">This month</div>
              <div class="font-mono font-semibold">{{ formatUsd(overview.cards?.month?.cost_usd) }}</div>
            </div>
            <div>
              <div class="text-xs uppercase text-ink-500">Today</div>
              <div class="font-mono font-semibold">{{ formatUsd(overview.cards?.today?.cost_usd) }}</div>
            </div>
            <div>
              <div class="text-xs uppercase text-ink-500">Active jobs</div>
              <div class="font-mono font-semibold">{{ overview.active_jobs ?? 0 }}</div>
            </div>
          </div>
        </div>
      </section>

      <section
        v-if="!embedded"
        class="rounded-md border px-5 py-4"
        :class="{
          'border-ink-200 bg-white dark:border-ink-800 dark:bg-ink-900': budgetTone === 'neutral' || budgetTone === 'ok',
          'border-amber-300 bg-amber-50/80 dark:border-amber-900': budgetTone === 'warn',
          'border-red-300 bg-red-50/80 dark:border-red-900': budgetTone === 'danger' || budgetTone === 'blocked',
        }"
      >
        <h2 class="font-display text-lg font-bold">Monthly budget</h2>
        <template v-if="overview.budget.enabled">
          <div class="mt-2 flex flex-wrap items-baseline justify-between gap-2">
            <div class="font-display text-2xl font-bold">
              {{ formatUsd(overview.budget.used) }} / {{ formatUsd(overview.budget.limit) }}
            </div>
            <div class="text-sm text-ink-600 dark:text-ink-300">
              {{ (overview.budget.percent_used || 0).toFixed(1) }}% used · {{ formatUsd(overview.budget.remaining) }} remaining
            </div>
          </div>
          <div class="mt-3 h-2.5 overflow-hidden rounded bg-ink-100 dark:bg-ink-800">
            <div class="h-full bg-accent" :style="{ width: `${Math.min(100, overview.budget.percent_used || 0)}%` }" />
          </div>
        </template>
        <p v-else class="mt-2 text-sm text-ink-600 dark:text-ink-300">Monthly budget is off.</p>
      </section>

      <template v-if="overview.empty">
        <div class="rounded-xl border border-ink-200 bg-white/80 p-8 text-center dark:border-ink-800 dark:bg-ink-900/60">
          <p class="font-display text-lg font-semibold">No AI activity yet.</p>
          <p class="mt-2 text-sm text-ink-600 dark:text-ink-300">
            Configure a model and run a translation to start collecting AI statistics.
          </p>
          <RouterLink class="mt-4 inline-block rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white" to="/settings/models">
            Configure models
          </RouterLink>
        </div>
      </template>

      <template v-else>
        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <h2 class="font-display text-lg font-bold">Model performance</h2>
          <p class="mt-1 text-xs text-ink-500">
            Display only. User priority still controls routing. Mean time is the average wall-clock duration of successful finished translation jobs.
          </p>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full text-left text-sm">
              <thead class="text-xs uppercase text-ink-500">
                <tr>
                  <th
                    v-for="column in rankingColumns"
                    :key="column.key"
                    class="py-2 pr-3"
                    :title="column.title"
                    :aria-sort="rankingSort?.key === column.key ? (rankingSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'"
                  >
                    <button
                      type="button"
                      class="inline-flex items-center gap-1 hover:text-ink-900 dark:hover:text-white"
                      :aria-label="`Sort by ${column.label}${rankingSortLabel(column.key)}`"
                      @click="sortRanking(column.key)"
                    >
                      {{ column.label }}
                      <span aria-hidden="true">{{ rankingSort?.key === column.key ? (rankingSort.direction === 'asc' ? '↑' : '↓') : '↕' }}</span>
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in sortedRanking" :key="`${row.provider_id || 'openrouter'}:${row.model_id}`" class="border-t border-ink-100 dark:border-ink-800">
                  <td class="py-2 pr-3">
                    <span v-if="row.configured_priority != null" class="rounded bg-ink-100 px-1.5 py-0.5 text-xs font-semibold dark:bg-ink-800">
                      Priority #{{ row.configured_priority }}
                    </span>
                    <span v-else class="text-ink-400">—</span>
                  </td>
                  <td class="py-2 pr-3">
                    <span
                      v-if="row.confidence !== 'insufficient' && row.adaptive_rank != null"
                      class="rounded bg-accent/15 px-1.5 py-0.5 text-xs font-semibold text-accent"
                    >
                      Adaptive #{{ row.adaptive_rank }}
                    </span>
                    <span v-else class="text-xs text-ink-500">insufficient data</span>
                  </td>
                  <td class="py-2 pr-3">{{ row.provider_name || row.provider_id || 'OpenRouter' }}</td>
                  <td class="py-2 pr-3 font-medium">{{ row.model_id }}</td>
                  <td class="py-2 pr-3">{{ formatPct(row.clean_success_rate) }}</td>
                  <td class="py-2 pr-3">{{ formatUsd(row.average_cost_per_clean_success_usd, 4) }}</td>
                  <td class="py-2 pr-3">{{ formatLatency(row.average_latency_ms) }}</td>
                  <td class="py-2 pr-3">
                    <button
                      v-if="row.completed_job_count"
                      type="button"
                      class="font-medium text-accent hover:underline"
                      @click="openJobTimes(row)"
                    >
                      {{ formatDuration(row.average_job_duration_seconds) }}
                      <span class="text-xs font-normal text-ink-500">
                        · {{ row.completed_job_count }}
                      </span>
                    </button>
                    <span v-else class="text-ink-400">—</span>
                  </td>
                  <td class="py-2 pr-3">{{ row.sample_count }} · {{ row.confidence }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <div class="grid gap-4 lg:grid-cols-2">
          <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
            <h2 class="font-display text-lg font-bold">Cost over time</h2>
            <div v-if="!costs?.series.length" class="mt-4 text-sm text-ink-500">No cost data in this period.</div>
            <template v-else>
              <svg viewBox="0 0 400 120" class="mt-4 h-32 w-full text-accent">
                <polyline
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  :points="(costs?.series || []).map((p, i) => `${(i / Math.max(1, (costs?.series.length || 1) - 1)) * 400},${120 - (p.cost_usd / maxCost) * 100}`).join(' ')"
                />
                <circle
                  v-for="(p, i) in (costs?.series || [])"
                  :key="p.date"
                  :cx="(i / Math.max(1, (costs?.series.length || 1) - 1)) * 400"
                  :cy="120 - (p.cost_usd / maxCost) * 100"
                  r="4"
                  class="fill-accent cursor-pointer"
                  @mouseenter="hoverPoint = p"
                  @mouseleave="hoverPoint = null"
                />
              </svg>
              <p v-if="hoverPoint" class="mt-2 text-xs text-ink-600 dark:text-ink-300">
                {{ hoverPoint.date }} · {{ formatUsd(hoverPoint.cost_usd, 4) }} · {{ hoverPoint.request_count ?? 0 }} requests
              </p>
            </template>
          </section>

          <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
            <h2 class="font-display text-lg font-bold">Recent AI activity</h2>
            <ul v-if="overview.routing.length" class="mt-3 space-y-2 text-sm">
              <li v-for="event in overview.routing" :key="event.id" class="flex flex-wrap gap-2">
                <span class="text-ink-500">{{ event.created_at ? formatDateTime(event.created_at) : '—' }}</span>
                <span class="font-medium">{{ event.provider_id || 'openrouter' }} / {{ event.model_id || '—' }}</span>
                <span>{{ event.event }}</span>
                <span v-if="event.next_model_id" class="text-ink-500">
                  → {{ event.next_provider_id || event.provider_id || 'openrouter' }} / {{ event.next_model_id }}
                </span>
                <span v-if="event.failure_category" class="text-amber-700 dark:text-amber-300">{{ event.failure_category }}</span>
              </li>
            </ul>
            <p v-else class="mt-3 text-sm text-ink-500">No routing events yet.</p>
          </section>
        </div>
      </template>
    </template>

    <div
      v-if="jobTimes"
      class="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/50 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="job-times-title"
      @click.self="closeJobTimes"
    >
      <div
        class="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-ink-200 bg-white p-5 shadow-xl dark:border-ink-700 dark:bg-ink-900"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 id="job-times-title" class="break-words font-display text-lg font-bold">
              Mean time · {{ jobTimes.model_id }}
            </h2>
            <p class="mt-1 text-sm text-ink-500">
              {{ formatDuration(jobTimes.average_job_duration_seconds) }}
              across {{ jobTimes.completed_job_count }}
              successful finished translation {{ jobTimes.completed_job_count === 1 ? 'job' : 'jobs' }}.
              <span v-if="jobTimes.items.length < jobTimes.completed_job_count">
                Showing the {{ jobTimes.items.length }} most recent.
              </span>
            </p>
          </div>
          <button
            type="button"
            class="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
            @click="closeJobTimes"
          >
            Close
          </button>
        </div>
        <p v-if="jobTimesError" class="mt-4 text-sm text-red-700 dark:text-red-300">{{ jobTimesError }}</p>
        <p v-else-if="jobTimesLoading" class="mt-4 text-sm text-ink-500">Loading jobs…</p>
        <div v-else class="mt-4 min-h-0 flex-1 overflow-auto">
          <table class="min-w-full text-left text-sm">
            <thead class="sticky top-0 bg-white text-xs uppercase text-ink-500 dark:bg-ink-900">
              <tr>
                <th class="py-2 pr-3">Completed</th>
                <th class="py-2 pr-3">Media</th>
                <th class="py-2 pr-3">Duration</th>
                <th class="py-2 pr-3">Trigger</th>
                <th class="py-2">Job</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in jobTimes.items"
                :key="item.job_id"
                class="border-t border-ink-100 dark:border-ink-800"
              >
                <td class="py-2 pr-3 whitespace-nowrap">
                  {{ item.completed_at ? formatDateTime(item.completed_at) : '—' }}
                </td>
                <td class="py-2 pr-3">{{ item.media_title || `Job #${item.job_id}` }}</td>
                <td class="py-2 pr-3 whitespace-nowrap">{{ formatDuration(item.duration_seconds) }}</td>
                <td class="py-2 pr-3">{{ item.trigger_type }}</td>
                <td class="py-2">
                  <RouterLink class="font-medium text-accent hover:underline" :to="`/jobs/${item.job_id}`">
                    #{{ item.job_id }}
                  </RouterLink>
                </td>
              </tr>
              <tr v-if="!jobTimes.items.length">
                <td class="py-3 text-ink-500" colspan="5">No completed jobs in this period.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>
