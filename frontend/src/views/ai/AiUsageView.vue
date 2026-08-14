<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '../../services/api'
import type { AiUsagePage } from '../../types'
import { formatDateTime } from '../../utils/datetime'

const page = ref<AiUsagePage | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)
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

async function load() {
  loading.value = true
  error.value = null
  try {
    page.value = await api.getAiUsage({ ...filters })
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
        <option value="today">Today</option>
        <option value="7d">7 days</option>
        <option value="30d">30 days</option>
        <option value="month">This month</option>
        <option value="all">All time</option>
      </select>
      <input v-model="filters.model" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" placeholder="Model" @change="load" />
      <select v-model="filters.tier" class="rounded-md border border-ink-300 bg-transparent px-2 py-1 text-sm dark:border-ink-600" @change="load">
        <option value="">Free/Paid</option>
        <option value="free">Free</option>
        <option value="paid">Paid</option>
        <option value="unknown">Unknown</option>
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
    </div>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">{{ error }}</p>
    <p v-else-if="loading" class="text-ink-500">Loading usage…</p>

    <template v-else-if="page">
      <section class="overflow-x-auto rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-bold">Model breakdown</h2>
        <table class="mt-3 min-w-full text-left text-sm">
          <thead class="text-xs uppercase text-ink-500">
            <tr>
              <th class="py-2 pr-3">Model</th>
              <th class="py-2 pr-3">Jobs</th>
              <th class="py-2 pr-3">Success</th>
              <th class="py-2 pr-3">Tokens</th>
              <th class="py-2 pr-3">Cost</th>
              <th class="py-2 pr-3">Avg</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in page.by_model" :key="row.model_id" class="border-t border-ink-100 dark:border-ink-800">
              <td class="py-2 pr-3 font-medium">{{ row.model_id }}</td>
              <td class="py-2 pr-3">{{ row.requests }}</td>
              <td class="py-2 pr-3">{{ (row.success_rate * 100).toFixed(0) }}%</td>
              <td class="py-2 pr-3">{{ row.total_tokens }}</td>
              <td class="py-2 pr-3">{{ formatUsd(row.cost_usd) }}</td>
              <td class="py-2 pr-3">{{ row.average_latency_ms ? `${(row.average_latency_ms / 1000).toFixed(1)}s` : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="overflow-x-auto rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-bold">Recent AI requests</h2>
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
              <th class="py-2 pr-3">Status</th>
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
              <td class="py-2 pr-3">{{ row.status }}{{ row.failure_category ? ` (${row.failure_category})` : '' }}</td>
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
