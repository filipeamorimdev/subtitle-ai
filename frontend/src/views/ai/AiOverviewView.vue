<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../../services/api'
import type { AiCosts, AiOverview } from '../../types'
import { formatDateTime } from '../../utils/datetime'

const period = ref('month')
const overview = ref<AiOverview | null>(null)
const costs = ref<AiCosts | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)

function formatUsd(n: number | null | undefined, digits = 2): string {
  if (n == null) return '—'
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n >= 0.01) return `$${n.toFixed(3)}`
  if (n === 0) return '$0'
  return `$${n.toFixed(digits)}`
}

function formatTokens(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`
  return String(n)
}

function formatPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${(n * 100).toFixed(1)}%`
}

const budgetTone = computed(() => {
  const pct = overview.value?.budget.percent_used
  if (pct == null) return 'neutral'
  if (pct >= 100) return 'blocked'
  if (pct >= 90) return 'danger'
  if (pct >= 70) return 'warn'
  return 'ok'
})

const maxCost = computed(() => Math.max(0.0001, ...(costs.value?.series.map((s) => s.cost_usd) || [0])))

async function load() {
  loading.value = true
  error.value = null
  try {
    const costPeriod = period.value === 'month' ? 'month' : period.value === 'today' ? 'today' : period.value === 'all' ? 'all' : period.value
    overview.value = await api.getAiOverview(period.value)
    costs.value = await api.getAiCosts(costPeriod === 'month' ? '30d' : costPeriod)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
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

    <template v-else-if="overview?.empty">
      <div class="rounded-xl border border-ink-200 bg-white/80 p-8 text-center dark:border-ink-800 dark:bg-ink-900/60">
        <p class="font-display text-lg font-semibold">No AI activity yet.</p>
        <p class="mt-2 text-sm text-ink-600 dark:text-ink-300">
          Configure a model and run a translation to start collecting AI statistics.
        </p>
        <RouterLink class="mt-4 inline-block rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white" to="/ai/models">
          Configure models
        </RouterLink>
      </div>
    </template>

    <template v-else-if="overview">
      <div class="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">This month</div>
          <div class="mt-1 font-display text-2xl font-bold">{{ formatUsd(overview.cards?.month?.cost_usd) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">This week</div>
          <div class="mt-1 font-display text-2xl font-bold">{{ formatUsd(overview.cards?.week?.cost_usd) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Today</div>
          <div class="mt-1 font-display text-2xl font-bold">{{ formatUsd(overview.cards?.today?.cost_usd) }}</div>
        </article>
        <article class="rounded-xl border px-4 py-4" :class="{
          'border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60': budgetTone === 'neutral' || budgetTone === 'ok',
          'border-amber-300 bg-amber-50/80 dark:border-amber-900': budgetTone === 'warn',
          'border-red-300 bg-red-50/80 dark:border-red-900': budgetTone === 'danger' || budgetTone === 'blocked',
        }">
          <div class="text-xs uppercase tracking-wide text-ink-500">Budget</div>
          <div class="mt-1 font-display text-2xl font-bold">
            <template v-if="overview.budget.enabled">{{ (overview.budget.percent_used || 0).toFixed(1) }}% used</template>
            <template v-else>Off</template>
          </div>
          <div v-if="overview.budget.enabled" class="mt-2 h-2 overflow-hidden rounded bg-ink-100 dark:bg-ink-800">
            <div class="h-full bg-accent" :style="{ width: `${Math.min(100, overview.budget.percent_used || 0)}%` }" />
          </div>
          <p v-if="overview.budget.enabled" class="mt-1 text-xs text-ink-500">
            {{ formatUsd(overview.budget.used) }} of {{ formatUsd(overview.budget.limit) }}
            · {{ formatUsd(overview.budget.remaining) }} remaining
          </p>
        </article>
      </div>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-bold">Cost over time</h2>
        <div v-if="!costs?.series.length" class="mt-4 text-sm text-ink-500">No cost data in this period.</div>
        <svg v-else viewBox="0 0 400 120" class="mt-4 h-32 w-full text-accent">
          <polyline
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            :points="costs.series.map((p, i) => `${(i / Math.max(1, costs.series.length - 1)) * 400},${120 - (p.cost_usd / maxCost) * 100}`).join(' ')"
          />
        </svg>
      </section>

      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Requests</div>
          <div class="mt-1 font-display text-2xl font-bold">{{ overview.requests }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Tokens</div>
          <div class="mt-1 font-display text-2xl font-bold">{{ formatTokens(overview.tokens.total) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Success rate</div>
          <div class="mt-1 font-display text-2xl font-bold">{{ formatPct(overview.success_rate) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Avg cost / translation</div>
          <div class="mt-1 font-display text-2xl font-bold">{{ formatUsd(overview.average_cost_usd, 4) }}</div>
        </article>
      </div>

      <div class="grid gap-3 sm:grid-cols-3">
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Free requests</div>
          <div class="mt-1 font-display text-xl font-bold">{{ overview.free_requests }}</div>
          <div class="text-xs text-ink-500">{{ formatTokens(overview.free_tokens) }} tokens</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Paid requests</div>
          <div class="mt-1 font-display text-xl font-bold">{{ overview.paid_requests }}</div>
          <div class="text-xs text-ink-500">{{ formatTokens(overview.paid_tokens) }} tokens</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase tracking-wide text-ink-500">Paid cost</div>
          <div class="mt-1 font-display text-xl font-bold">{{ formatUsd(overview.paid_cost_usd) }}</div>
        </article>
      </div>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-bold">Adaptive ranking</h2>
        <p class="mt-1 text-xs text-ink-500">Display only. User priority still controls routing.</p>
        <div class="mt-4 overflow-x-auto">
          <table class="min-w-full text-left text-sm">
            <thead class="text-xs uppercase text-ink-500">
              <tr>
                <th class="py-2 pr-3">Configured</th>
                <th class="py-2 pr-3">Adaptive</th>
                <th class="py-2 pr-3">Model</th>
                <th class="py-2 pr-3">Score</th>
                <th class="py-2 pr-3">Clean</th>
                <th class="py-2 pr-3">Cost</th>
                <th class="py-2 pr-3">Samples</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in overview.ranking" :key="row.model_id" class="border-t border-ink-100 dark:border-ink-800">
                <td class="py-2 pr-3">#{{ idx + 1 }}</td>
                <td class="py-2 pr-3">{{ row.confidence === 'insufficient' ? 'insufficient data' : `#${row.adaptive_rank}` }}</td>
                <td class="py-2 pr-3 font-medium">{{ row.model_id }}</td>
                <td class="py-2 pr-3">{{ row.adaptive_score != null ? Math.round(row.adaptive_score) : '—' }}</td>
                <td class="py-2 pr-3">{{ formatPct(row.clean_success_rate) }}</td>
                <td class="py-2 pr-3">{{ formatUsd(row.average_cost_per_clean_success_usd, 4) }}</td>
                <td class="py-2 pr-3">{{ row.sample_count }} · {{ row.confidence }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-bold">Routing activity</h2>
        <ul v-if="overview.routing.length" class="mt-3 space-y-2 text-sm">
          <li v-for="event in overview.routing" :key="event.id" class="flex flex-wrap gap-2">
            <span class="text-ink-500">{{ event.created_at ? formatDateTime(event.created_at) : '—' }}</span>
            <span class="font-medium">{{ event.model_id || '—' }}</span>
            <span>{{ event.event }}</span>
            <span v-if="event.next_model_id" class="text-ink-500">→ {{ event.next_model_id }}</span>
            <span v-if="event.failure_category" class="text-amber-700 dark:text-amber-300">{{ event.failure_category }}</span>
          </li>
        </ul>
        <p v-else class="mt-3 text-sm text-ink-500">No routing events yet.</p>
      </section>
    </template>
  </div>
</template>
