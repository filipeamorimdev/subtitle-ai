<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import type { JobLog, JobUsage, JobUsageExchange } from '../types'
import { formatDateTime } from '../utils/datetime'

const props = defineProps<{ id: string }>()

const usage = ref<JobUsage | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)
const jobLog = ref<JobLog | null>(null)
const requestBusy = ref(false)
const requestModal = ref<{ title: string; body: string; error: string | null } | null>(null)

const MODEL_COLORS = [
  '#34d399',
  '#fb923c',
  '#f87171',
  '#60a5fa',
  '#f472b6',
  '#a78bfa',
  '#fbbf24',
  '#2dd4bf',
]

const ACTION_LABELS: Record<string, string> = {
  translate: 'Translate',
  repair: 'Repair',
  glossary_extract: 'Glossary extract',
  glossary_universe: 'Universe classify',
  other: 'Other',
}

function formatTokens(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`
  return String(n)
}

function formatUsd(n: number | null | undefined, digits = 4): string {
  if (n == null) return '—'
  if (n >= 1) return `$${n.toFixed(2)}`
  if (n >= 0.01) return `$${n.toFixed(3)}`
  return `$${n.toFixed(digits)}`
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] || action
}

function modelColor(model: string): string {
  const models = usage.value?.by_model.map((m) => m.model) || []
  const idx = models.indexOf(model)
  return MODEL_COLORS[idx >= 0 ? idx % MODEL_COLORS.length : 0]
}

function actionColor(action: string): string {
  const actions = usage.value?.by_action.map((a) => a.action) || []
  const idx = actions.indexOf(action)
  return MODEL_COLORS[(idx >= 0 ? idx : 0) % MODEL_COLORS.length]
}

const pricingNote = computed(() => {
  switch (usage.value?.pricing_source) {
    case 'openrouter':
      return 'Cost from OpenRouter usage'
    case 'estimated':
      return 'Cost estimated from current model prices'
    case 'mixed':
      return 'Mixed: reported + estimated costs'
    default:
      return 'Cost unavailable'
  }
})

const maxModelTokens = computed(() =>
  Math.max(1, ...(usage.value?.by_model.map((m) => m.total_tokens) || [1])),
)

const maxActionTokens = computed(() =>
  Math.max(1, ...(usage.value?.by_action.map((a) => a.total_tokens) || [1])),
)

const CHART_HEIGHT_PX = 160

const exchangeBars = computed(() => {
  const rows = usage.value?.exchanges || []
  if (!rows.length) return []
  const max = Math.max(1, ...rows.map((r) => r.total_tokens || r.cost_usd || 0))
  return rows.map((row) => ({
    ...row,
    heightPx: Math.max(6, ((row.total_tokens || 0) / max) * CHART_HEIGHT_PX),
  }))
})

const kpiCards = computed(() => {
  const t = usage.value?.totals
  if (!t) return []
  return [
    {
      label: 'Total spend',
      value: formatUsd(t.cost_usd),
      hint: pricingNote.value,
    },
    {
      label: 'Requests',
      value: formatTokens(t.requests),
      hint: `${t.requests} API call${t.requests === 1 ? '' : 's'}`,
    },
    {
      label: 'Token volume',
      value: formatTokens(t.total_tokens),
      hint: `in ${formatTokens(t.input_tokens)} / out ${formatTokens(t.output_tokens)}`,
    },
    {
      label: 'Blended $/1M',
      value: formatUsd(t.blended_cost_per_million, 2),
      hint: 'Cost per million tokens',
    },
  ]
})

async function load() {
  loading.value = true
  error.value = null
  try {
    usage.value = await api.getJobUsage(Number(props.id))
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    usage.value = null
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.id, () => {
  jobLog.value = null
  requestModal.value = null
  load()
})

function exchangeTitle(row: JobUsageExchange): string {
  return `#${row.index} ${actionLabel(row.action)} · ${row.model}`
}

function logTimestamp(value: unknown): string {
  return String(value ?? '')
    .replace('T', ' ')
    .slice(0, 19)
}

function findExchangeRequest(entries: Record<string, unknown>[] | null | undefined, row: JobUsageExchange): unknown {
  const exchanges = (entries || []).filter((entry) => entry.event === 'exchange')
  const rowTs = logTimestamp(row.ts)
  const matched =
    (rowTs ? exchanges.find((entry) => logTimestamp(entry.ts) === rowTs) : undefined) ||
    exchanges[row.index - 1]
  return matched?.request ?? null
}

async function viewRequest(row: JobUsageExchange) {
  requestBusy.value = true
  try {
    if (!jobLog.value) {
      jobLog.value = await api.getJobLog(Number(props.id))
    }
    if (!jobLog.value.exists) {
      requestModal.value = {
        title: exchangeTitle(row),
        body: '',
        error: 'No OpenRouter log for this job. Enable exchange logging and rerun to inspect requests.',
      }
      return
    }
    const request = findExchangeRequest(jobLog.value.entries, row)
    if (request == null) {
      requestModal.value = {
        title: exchangeTitle(row),
        body: '',
        error: 'No request payload was recorded for this exchange.',
      }
      return
    }
    requestModal.value = {
      title: exchangeTitle(row),
      body: JSON.stringify(request, null, 2),
      error: null,
    }
  } catch (err) {
    requestModal.value = {
      title: exchangeTitle(row),
      body: '',
      error: err instanceof Error ? err.message : String(err),
    }
  } finally {
    requestBusy.value = false
  }
}
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0">
        <p class="text-sm text-ink-500">
          <RouterLink class="text-accent hover:underline" :to="`/jobs/${id}`">← Job #{{ id }}</RouterLink>
        </p>
        <h1 class="mt-1 break-words font-display text-2xl font-bold sm:text-3xl">
          {{ usage?.media_title || 'Job' }} usage
        </h1>
        <p class="mt-1 text-sm text-ink-600 sm:text-base dark:text-ink-300">
          Tokens and cost for every OpenRouter action in this job
          <span v-if="usage"> · {{ usage.job_kind }} · {{ usage.status }}</span>
        </p>
      </div>
      <RouterLink
        class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
        :to="`/jobs/${id}`"
      >
        Job details
      </RouterLink>
    </div>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>
    <p v-else-if="loading" class="text-ink-500">Loading usage…</p>

    <template v-else-if="usage">
      <div class="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
        <article
          v-for="card in kpiCards"
          :key="card.label"
          class="rounded-xl border border-ink-200 bg-white/80 px-3 py-3 dark:border-ink-800 dark:bg-ink-900/60 sm:px-4 sm:py-4"
        >
          <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">{{ card.label }}</div>
          <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ card.value }}</div>
          <div class="mt-1 text-xs text-ink-500">{{ card.hint }}</div>
        </article>
      </div>

      <div class="grid gap-4 lg:grid-cols-2">
        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h2 class="font-display text-lg font-bold">Usage by model</h2>
              <p class="mt-1 text-sm text-ink-500">Token share across models used in this job</p>
            </div>
            <p class="text-sm text-ink-500">{{ usage.by_model.length }} model{{ usage.by_model.length === 1 ? '' : 's' }}</p>
          </div>

          <p v-if="!usage.by_model.length" class="mt-6 text-sm text-ink-500">
            No model usage recorded yet.
          </p>
          <ul v-else class="mt-5 space-y-4">
            <li v-for="item in usage.by_model" :key="item.model">
              <div class="flex items-center justify-between gap-3 text-sm">
                <div class="min-w-0">
                  <div class="truncate font-medium">{{ item.name || item.model }}</div>
                  <div class="truncate text-xs text-ink-500">{{ item.model }}</div>
                </div>
                <div class="shrink-0 text-right">
                  <div>{{ formatTokens(item.total_tokens) }} tok</div>
                  <div class="text-xs text-ink-500">{{ formatUsd(item.cost_usd) }}</div>
                </div>
              </div>
              <div class="mt-2 h-2 overflow-hidden rounded-full bg-ink-100 dark:bg-ink-800">
                <div
                  class="h-full rounded-full transition-all"
                  :style="{
                    width: `${(item.total_tokens / maxModelTokens) * 100}%`,
                    backgroundColor: modelColor(item.model),
                  }"
                />
              </div>
              <div class="mt-1 text-xs text-ink-500">
                {{ item.requests }} req · in {{ formatTokens(item.input_tokens) }} / out
                {{ formatTokens(item.output_tokens) }}
              </div>
            </li>
          </ul>
        </section>

        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h2 class="font-display text-lg font-bold">Usage by action</h2>
              <p class="mt-1 text-sm text-ink-500">Glossary, translate, and repair calls</p>
            </div>
            <p class="text-sm text-ink-500">{{ usage.by_action.length }} kinds</p>
          </div>

          <p v-if="!usage.by_action.length" class="mt-6 text-sm text-ink-500">
            No actions recorded yet.
          </p>
          <ul v-else class="mt-5 space-y-4">
            <li v-for="item in usage.by_action" :key="item.action">
              <div class="flex items-center justify-between gap-3 text-sm">
                <div class="font-medium capitalize">{{ actionLabel(item.action) }}</div>
                <div class="shrink-0 text-right">
                  <div>{{ formatTokens(item.total_tokens) }} tok</div>
                  <div class="text-xs text-ink-500">{{ formatUsd(item.cost_usd) }}</div>
                </div>
              </div>
              <div class="mt-2 h-2 overflow-hidden rounded-full bg-ink-100 dark:bg-ink-800">
                <div
                  class="h-full rounded-full transition-all"
                  :style="{
                    width: `${(item.total_tokens / maxActionTokens) * 100}%`,
                    backgroundColor: actionColor(item.action),
                  }"
                />
              </div>
              <div class="mt-1 text-xs text-ink-500">{{ item.requests }} requests</div>
            </li>
          </ul>
        </section>
      </div>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="font-display text-lg font-bold">Request volume by action</h2>
            <p class="mt-1 text-sm text-ink-500">Each bar is one OpenRouter exchange in job order</p>
          </div>
          <div class="flex flex-wrap gap-3 text-xs text-ink-500">
            <span
              v-for="item in usage.by_action"
              :key="`legend-${item.action}`"
              class="inline-flex items-center gap-1.5"
            >
              <span
                class="inline-block h-2.5 w-2.5 rounded-sm"
                :style="{ backgroundColor: actionColor(item.action) }"
              />
              {{ actionLabel(item.action) }}
            </span>
          </div>
        </div>

        <p v-if="!exchangeBars.length" class="mt-6 text-sm text-ink-500">
          <template v-if="!usage.log_exists">
            No OpenRouter log for this job. Stats appear after translation API calls run.
          </template>
          <template v-else>Log exists but has no exchange entries yet.</template>
        </p>
        <div v-else class="mt-6 flex items-end gap-1 overflow-x-auto pb-1" :style="{ height: `${CHART_HEIGHT_PX}px` }">
          <div
            v-for="bar in exchangeBars"
            :key="bar.index"
            class="group relative flex min-w-[10px] flex-1 flex-col justify-end"
            :title="`${exchangeTitle(bar)} · ${formatTokens(bar.total_tokens)} tok · ${formatUsd(bar.cost_usd)}`"
          >
            <div
              class="w-full rounded-t-sm transition-opacity group-hover:opacity-80"
              :style="{
                height: `${bar.heightPx}px`,
                backgroundColor: actionColor(bar.action),
                opacity: bar.ok ? 1 : 0.35,
              }"
            />
          </div>
        </div>
      </section>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="font-display text-lg font-bold">Exchanges</h2>
            <p class="mt-1 text-sm text-ink-500">Per-call tokens and cost</p>
          </div>
          <p class="text-sm text-ink-500">{{ usage.exchanges.length }} total</p>
        </div>

        <div class="mt-4 overflow-x-auto">
          <table class="min-w-full text-left text-sm">
            <thead class="border-b border-ink-200 text-ink-500 dark:border-ink-800 dark:text-ink-300">
              <tr>
                <th class="py-2 pr-4 font-medium">#</th>
                <th class="py-2 pr-4 font-medium">Time</th>
                <th class="py-2 pr-4 font-medium">Action</th>
                <th class="py-2 pr-4 font-medium">Model</th>
                <th class="py-2 pr-4 font-medium">Tokens</th>
                <th class="py-2 pr-4 font-medium">Cost</th>
                <th class="py-2 font-medium">Request</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!usage.exchanges.length">
                <td colspan="7" class="py-4 text-ink-500">No exchanges recorded.</td>
              </tr>
              <tr
                v-for="row in usage.exchanges"
                :key="row.index"
                class="border-b border-ink-100 last:border-0 dark:border-ink-800/80"
              >
                <td class="py-3 pr-4 align-top text-ink-500">{{ row.index }}</td>
                <td class="py-3 pr-4 align-top whitespace-nowrap text-ink-600 dark:text-ink-300">
                  {{ formatDateTime(row.ts) }}
                </td>
                <td class="py-3 pr-4 align-top">
                  <span class="inline-flex items-center gap-1.5 capitalize">
                    <span
                      class="inline-block h-2 w-2 rounded-sm"
                      :style="{ backgroundColor: actionColor(row.action) }"
                    />
                    {{ actionLabel(row.action) }}
                  </span>
                  <span v-if="!row.ok" class="ml-2 text-xs text-red-600 dark:text-red-300">
                    {{ row.error || 'failed' }}
                  </span>
                </td>
                <td class="py-3 pr-4 align-top truncate max-w-[14rem]" :title="row.model">
                  {{ row.model }}
                </td>
                <td class="py-3 pr-4 align-top whitespace-nowrap">
                  {{ formatTokens(row.total_tokens) }}
                  <span class="text-ink-500">
                    ({{ formatTokens(row.input_tokens) }}/{{ formatTokens(row.output_tokens) }})
                  </span>
                </td>
                <td class="py-3 pr-4 align-top whitespace-nowrap">
                  {{ formatUsd(row.cost_usd) }}
                  <span v-if="row.cost_estimated" class="text-xs text-ink-500">est.</span>
                </td>
                <td class="py-3 align-top">
                  <button
                    type="button"
                    class="rounded-md border border-ink-300 px-2 py-1 text-xs font-semibold dark:border-ink-600"
                    :disabled="requestBusy"
                    @click="viewRequest(row)"
                  >
                    View request
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <div
      v-if="requestModal"
      class="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/50 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="exchange-request-title"
      @click.self="requestModal = null"
    >
      <div
        class="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-ink-200 bg-white p-5 shadow-xl dark:border-ink-700 dark:bg-ink-900"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 id="exchange-request-title" class="break-words font-display text-lg font-bold">
              {{ requestModal.title }}
            </h2>
            <p class="mt-1 text-sm text-ink-500">OpenRouter request for this exchange</p>
          </div>
          <button
            type="button"
            class="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
            @click="requestModal = null"
          >
            Close
          </button>
        </div>
        <p v-if="requestModal.error" class="mt-4 text-sm text-red-700 dark:text-red-300">
          {{ requestModal.error }}
        </p>
        <pre
          v-else
          class="mt-4 min-h-0 flex-1 overflow-auto rounded-lg bg-ink-950 p-4 text-xs leading-relaxed text-ink-100"
        >{{ requestModal.body }}</pre>
      </div>
    </div>
  </section>
</template>
