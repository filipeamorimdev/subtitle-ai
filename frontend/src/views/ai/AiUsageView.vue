<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../../services/api'
import type { AiCosts, AiUsagePage } from '../../types'
import { formatDateTime } from '../../utils/datetime'

const page = ref<AiUsagePage | null>(null)
const costs = ref<AiCosts | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)
const customStart = ref('')
const customEnd = ref('')
const filters = reactive({
  period: '30d',
  model: '',
  tier: '',
  operation: '',
  trigger_type: '',
  status: '',
  failure: '',
  offset: 0,
  limit: 50,
  sort: 'cost_usd',
})

function formatUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n === 0) return '$0'
  return `$${n.toFixed(4)}`
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

const totals = computed(() => page.value?.totals)

const maxCost = computed(() => Math.max(0.0001, ...(costs.value?.series.map((s) => s.cost_usd) || [0])))
const maxModelCost = computed(() => Math.max(0.0001, ...(costs.value?.by_model.map((m) => m.cost_usd) || [0])))
const maxFail = computed(() => Math.max(1, ...(costs.value?.failure_categories?.map((f) => f.count) || [0])))

async function load() {
  loading.value = true
  error.value = null
  try {
    const params: Record<string, string | number | undefined> = { ...filters }
    if (filters.period === 'custom') {
      params.start = customStart.value || undefined
      params.end = customEnd.value || undefined
    }
    page.value = await api.getAiUsage(params)
    const costPeriod = filters.period === 'month' ? 'month' : filters.period
    costs.value = await api.getAiCosts(
      costPeriod,
      filters.period === 'custom'
        ? { start: customStart.value || undefined, end: customEnd.value || undefined }
        : {},
    )
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
    <div class="flex flex-wrap gap-2">
      <select v-model="filters.period" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="filters.offset = 0; load()">
        <option value="7d">7 days</option>
        <option value="30d">30 days</option>
        <option value="month">This month</option>
        <option value="all">All time</option>
        <option value="custom">Custom</option>
      </select>
      <template v-if="filters.period === 'custom'">
        <input v-model="customStart" type="date" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="load" />
        <input v-model="customEnd" type="date" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="load" />
      </template>
      <input v-model="filters.model" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" placeholder="Model" @change="load" />
      <select v-model="filters.tier" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="load">
        <option value="">Free/Paid</option>
        <option value="free">Free</option>
        <option value="paid">Paid</option>
        <option value="unknown">Unknown</option>
      </select>
      <select v-model="filters.operation" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="load">
        <option value="">Operation</option>
        <option value="translation">translation</option>
        <option value="translation_repair">translation_repair</option>
        <option value="glossary_extract">glossary_extract</option>
        <option value="glossary_universe">glossary_universe</option>
        <option value="model_test">model_test</option>
      </select>
      <select v-model="filters.trigger_type" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="load">
        <option value="">Automatic/Manual</option>
        <option value="automatic">Automatic</option>
        <option value="manual">Manual</option>
      </select>
      <select v-model="filters.status" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="load">
        <option value="">Status</option>
        <option value="success">Success</option>
        <option value="failed">Failed</option>
      </select>
      <input v-model="filters.failure" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" placeholder="Failure" @change="load" />
    </div>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">{{ error }}</p>
    <p v-else-if="loading" class="text-ink-500">Loading usage…</p>

    <template v-else-if="page">
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Requests</div>
          <div class="font-display text-xl font-bold">{{ totals?.requests ?? page.total }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Tokens</div>
          <div class="font-display text-xl font-bold">{{ formatTokens(totals?.total_tokens) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Cost</div>
          <div class="font-display text-xl font-bold">{{ formatUsd(totals?.cost_usd) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Success</div>
          <div class="font-display text-xl font-bold">{{ formatPct(totals?.success_rate) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Clean success</div>
          <div class="font-display text-xl font-bold">{{ formatPct(totals?.clean_success_rate) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Repair rate</div>
          <div class="font-display text-xl font-bold">{{ formatPct(totals?.repair_rate) }}</div>
        </article>
      </div>

      <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Clean success</div>
          <div class="font-semibold">{{ formatPct(totals?.clean_success_rate) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Repair rate</div>
          <div class="font-semibold">{{ formatPct(totals?.repair_rate) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Validation failure</div>
          <div class="font-semibold">{{ formatPct(totals?.validation_failure_rate) }}</div>
        </article>
        <article class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="text-xs uppercase text-ink-500">Technical failure</div>
          <div class="font-semibold">{{ formatPct(totals?.technical_failure_rate) }}</div>
        </article>
      </div>

      <div class="grid gap-4 lg:grid-cols-2">
        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <h2 class="font-display text-lg font-bold">Cost over time</h2>
          <svg v-if="costs?.series.length" viewBox="0 0 400 100" class="mt-3 h-28 w-full text-accent">
            <polyline
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              :points="costs.series.map((p, i) => `${(i / Math.max(1, costs.series.length - 1)) * 400},${100 - (p.cost_usd / maxCost) * 90}`).join(' ')"
            />
          </svg>
          <p v-else class="mt-3 text-sm text-ink-500">No cost series.</p>
        </section>
        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <h2 class="font-display text-lg font-bold">Cost by model</h2>
          <ul class="mt-3 space-y-2">
            <li v-for="m in (costs?.by_model || []).slice(0, 8)" :key="m.model_id">
              <div class="flex justify-between text-xs">
                <span class="truncate font-medium">{{ m.model_id }}</span>
                <span>{{ formatUsd(m.cost_usd) }}</span>
              </div>
              <div class="mt-1 h-1.5 rounded bg-ink-100 dark:bg-ink-800">
                <div class="h-full rounded bg-accent" :style="{ width: `${(m.cost_usd / maxModelCost) * 100}%` }" />
              </div>
            </li>
          </ul>
        </section>
        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <h2 class="font-display text-lg font-bold">Free vs paid</h2>
          <dl class="mt-3 grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt class="text-xs uppercase text-ink-500">Free requests</dt>
              <dd class="font-semibold">{{ costs?.free_vs_paid.free_requests ?? 0 }}</dd>
            </div>
            <div>
              <dt class="text-xs uppercase text-ink-500">Paid requests</dt>
              <dd class="font-semibold">{{ costs?.free_vs_paid.paid_requests ?? 0 }}</dd>
            </div>
            <div>
              <dt class="text-xs uppercase text-ink-500">Free cost</dt>
              <dd class="font-semibold">{{ formatUsd(costs?.free_vs_paid.free_cost_usd) }}</dd>
            </div>
            <div>
              <dt class="text-xs uppercase text-ink-500">Paid cost</dt>
              <dd class="font-semibold">{{ formatUsd(costs?.free_vs_paid.paid_cost_usd) }}</dd>
            </div>
          </dl>
        </section>
        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <h2 class="font-display text-lg font-bold">Failure categories</h2>
          <ul class="mt-3 space-y-2">
            <li v-for="f in costs?.failure_categories || []" :key="f.category">
              <div class="flex justify-between text-xs">
                <span>{{ f.category }}</span>
                <span>{{ f.count }}</span>
              </div>
              <div class="mt-1 h-1.5 rounded bg-ink-100 dark:bg-ink-800">
                <div class="h-full rounded bg-amber-500" :style="{ width: `${(f.count / maxFail) * 100}%` }" />
              </div>
            </li>
            <li v-if="!(costs?.failure_categories || []).length" class="text-sm text-ink-500">No failures in this period.</li>
          </ul>
        </section>
      </div>

      <section class="overflow-x-auto rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-bold">Model breakdown</h2>
        <table class="mt-3 min-w-full text-left text-sm">
          <thead class="text-xs uppercase text-ink-500">
            <tr>
              <th class="py-2 pr-3">Model</th>
              <th class="py-2 pr-3">Requests</th>
              <th class="py-2 pr-3">Success</th>
              <th class="py-2 pr-3">Clean</th>
              <th class="py-2 pr-3">Repair</th>
              <th class="py-2 pr-3">Cost</th>
              <th class="py-2 pr-3">Avg latency</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in page.by_model" :key="row.model_id" class="border-t border-ink-100 dark:border-ink-800">
              <td class="py-2 pr-3 font-medium">{{ row.model_id }}</td>
              <td class="py-2 pr-3">{{ row.requests }}</td>
              <td class="py-2 pr-3">{{ (row.success_rate * 100).toFixed(0) }}%</td>
              <td class="py-2 pr-3">{{ (row.clean_success_rate * 100).toFixed(0) }}%</td>
              <td class="py-2 pr-3">{{ (row.repair_rate * 100).toFixed(0) }}%</td>
              <td class="py-2 pr-3">{{ formatUsd(row.cost_usd) }}</td>
              <td class="py-2 pr-3">{{ row.average_latency_ms ? `${(row.average_latency_ms / 1000).toFixed(1)}s` : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="overflow-x-auto rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-bold">AI request history</h2>
        <table class="mt-3 min-w-full text-left text-sm">
          <thead class="text-xs uppercase text-ink-500">
            <tr>
              <th class="py-2 pr-3">Time</th>
              <th class="py-2 pr-3">Media</th>
              <th class="py-2 pr-3">Operation</th>
              <th class="py-2 pr-3">Model</th>
              <th class="py-2 pr-3">Free/Paid</th>
              <th class="py-2 pr-3">Trigger</th>
              <th class="py-2 pr-3">Tokens</th>
              <th class="py-2 pr-3">Cost</th>
              <th class="py-2 pr-3">Result</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in page.items" :key="row.id" class="border-t border-ink-100 dark:border-ink-800">
              <td class="py-2 pr-3 whitespace-nowrap">{{ row.created_at ? formatDateTime(row.created_at) : '—' }}</td>
              <td class="py-2 pr-3">{{ row.media_title || (row.job_id ? `#${row.job_id}` : '—') }}</td>
              <td class="py-2 pr-3">{{ row.operation_type }}</td>
              <td class="py-2 pr-3">{{ row.model_id }}</td>
              <td class="py-2 pr-3">{{ row.tier }}</td>
              <td class="py-2 pr-3">{{ row.trigger_type }}</td>
              <td class="py-2 pr-3">{{ row.total_tokens }}</td>
              <td class="py-2 pr-3">{{ formatUsd(row.cost_usd) }}</td>
              <td class="py-2 pr-3">{{ row.outcome || row.status }}{{ row.failure_category ? ` (${row.failure_category})` : '' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="mt-3 flex gap-2 text-sm">
          <button type="button" :disabled="filters.offset <= 0" @click="filters.offset = Math.max(0, filters.offset - filters.limit); load()">Previous</button>
          <span>{{ filters.offset + 1 }}–{{ Math.min(page.total, filters.offset + filters.limit) }} of {{ page.total }}</span>
          <button type="button" :disabled="filters.offset + filters.limit >= page.total" @click="filters.offset += filters.limit; load()">Next</button>
        </div>
      </section>
    </template>
  </div>
</template>
