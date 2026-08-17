<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../../services/api'
import type { AiModelsPayload, AiPreference, OpenRouterModel } from '../../types'

const data = ref<AiModelsPayload | null>(null)
const error = ref<string | null>(null)
const message = ref<string | null>(null)
const loading = ref(true)
const pickerOpen = ref(false)
const pickerQuery = ref('')
const pickerFilter = ref<'all' | 'compatible' | 'free' | 'paid'>('compatible')
const testResult = ref<Record<number, string>>({})

const routing = reactive({
  routing_strategy: 'free_first',
  allow_paid_fallback: false,
  allow_free_fallback: true,
  allow_unknown_pricing: false,
  maximum_cost_per_job_usd: 0.05 as number | null,
  monthly_budget_enabled: false,
  monthly_budget_amount_usd: 5 as number | null,
  allow_manual_budget_override: false,
})

function formatPrice(value: number | null | undefined): string {
  if (value == null) return 'Unknown'
  if (value <= 0) return '$0'
  if (value < 0.01) return `$${value.toFixed(4)}`
  if (value < 1) return `$${value.toFixed(3)}`
  return `$${value.toFixed(2)}`
}

function formatUsd(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n === 0) return '$0'
  if (n < 0.01) return `$${n.toFixed(4)}`
  return `$${n.toFixed(3)}`
}

function formatPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${(n * 100).toFixed(0)}%`
}

function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}

function badge(tier?: string | null, stale?: boolean, unavailable?: boolean) {
  if (unavailable) return 'UNAVAILABLE'
  if (stale) return 'STALE'
  return (tier || 'UNKNOWN').toUpperCase()
}

const freePool = computed(() => (data.value?.preferences || []).filter((p) => p.tier === 'free').sort((a, b) => a.priority - b.priority))
const paidPool = computed(() => (data.value?.preferences || []).filter((p) => p.tier === 'paid').sort((a, b) => a.priority - b.priority))
const usedIds = computed(() => new Set((data.value?.preferences || []).map((p) => p.model_id)))

const filteredCatalog = computed(() => {
  const models = data.value?.catalog || []
  const q = pickerQuery.value.trim().toLowerCase()
  return models.filter((m) => {
    if (usedIds.value.has(m.id)) return false
    if (pickerFilter.value === 'compatible' && m.compatible === false) return false
    if (pickerFilter.value === 'free' && m.pricing_tier !== 'free') return false
    if (pickerFilter.value === 'paid' && m.pricing_tier !== 'paid') return false
    if (!q) return true
    return m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)
  })
})

async function load() {
  loading.value = true
  error.value = null
  try {
    data.value = await api.getAiModels()
    Object.assign(routing, data.value.routing)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveRouting() {
  message.value = null
  error.value = null
  try {
    await api.updateAiRouting({
      ...routing,
      clear_maximum_cost_per_job: routing.maximum_cost_per_job_usd == null,
      clear_monthly_budget_amount: routing.monthly_budget_amount_usd == null,
    })
    message.value = 'Routing and cost controls saved.'
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function refresh() {
  message.value = null
  const result = await api.refreshAiModels()
  message.value = result.ok ? `Refreshed ${result.count} models.` : (result.message || 'Refresh failed; kept last catalog.')
  await load()
}

async function addModel(model: OpenRouterModel, tier: 'free' | 'paid') {
  error.value = null
  try {
    await api.addAiModel(model.id, tier)
    pickerOpen.value = false
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function toggle(pref: AiPreference) {
  await api.patchAiModel(pref.id, { enabled: !pref.enabled })
  await load()
}

async function remove(pref: AiPreference) {
  await api.deleteAiModel(pref.id)
  await load()
}

async function test(pref: AiPreference) {
  testResult.value[pref.id] = 'Testing…'
  const result = await api.testAiModel(pref.model_id)
  testResult.value[pref.id] = result.message
}

async function move(pref: AiPreference, dir: -1 | 1) {
  const pool = pref.tier === 'free' ? [...freePool.value] : [...paidPool.value]
  const idx = pool.findIndex((p) => p.id === pref.id)
  const next = idx + dir
  if (next < 0 || next >= pool.length) return
  const ids = pool.map((p) => p.id)
  const [item] = ids.splice(idx, 1)
  ids.splice(next, 0, item)
  await api.reorderAiModels(pref.tier as 'free' | 'paid', ids)
  await load()
}

function catalogAge(seconds: number | null): string {
  if (seconds == null) return 'never'
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  return `${Math.round(seconds / 3600)} hours ago`
}

function canAddFree(model: OpenRouterModel) {
  return model.compatible !== false && model.pricing_tier === 'free'
}

function canAddPaid(model: OpenRouterModel) {
  return model.compatible !== false && (model.pricing_tier === 'paid' || model.pricing_tier === 'unknown')
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">{{ error }}</p>
    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{{ message }}</p>
    <p v-if="loading" class="text-ink-500">Loading models…</p>

    <template v-else-if="data">
      <section
        v-if="!data.openrouter_configured"
        class="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100"
      >
        OpenRouter is not configured.
        <RouterLink class="font-semibold underline" to="/settings/models/providers">Configure providers</RouterLink>
        before translating.
      </section>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 class="font-display text-lg font-semibold">Catalog</h2>
            <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
              Connection: {{ data.openrouter_configured ? '● Configured' : 'Not configured' }}
              · Catalog: {{ catalogAge(data.catalog_age_seconds) }}
              <span v-if="data.catalog_stale || data.pricing_freshness === 'stale'" class="text-amber-700">
                (stale pricing)
              </span>
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <RouterLink
              class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
              to="/settings/models/providers"
            >
              Manage providers
            </RouterLink>
            <button class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600" type="button" @click="refresh">
              Refresh models
            </button>
            <button class="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white" type="button" @click="saveRouting">
              Save routing
            </button>
          </div>
        </div>
      </section>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-semibold">Routing</h2>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="text-sm">
            <span class="text-ink-500">Strategy</span>
            <select v-model="routing.routing_strategy" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600">
              <option value="free_first">Free first</option>
              <option value="paid_first">Paid first</option>
              <option value="free_only">Free only</option>
              <option value="paid_only">Paid only</option>
            </select>
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="routing.allow_paid_fallback" type="checkbox" />
            Allow paid fallback (can incur OpenRouter charges)
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="routing.allow_free_fallback" type="checkbox" />
            Allow free fallback
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="routing.allow_unknown_pricing" type="checkbox" />
            Allow unknown-priced models
          </label>
        </div>
        <h3 class="mt-5 font-display font-semibold">Cost controls</h3>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="text-sm">
            <span class="text-ink-500">Maximum cost per automatic translation (USD)</span>
            <input v-model.number="routing.maximum_cost_per_job_usd" type="number" min="0" step="0.01" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600" />
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="routing.monthly_budget_enabled" type="checkbox" />
            Enable monthly AI budget
          </label>
          <label class="text-sm">
            <span class="text-ink-500">Monthly budget (USD)</span>
            <input v-model.number="routing.monthly_budget_amount_usd" type="number" min="0" step="0.01" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600" />
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="routing.allow_manual_budget_override" type="checkbox" />
            Allow manual jobs to bypass budget
          </label>
        </div>
      </section>

      <div class="grid gap-4 lg:grid-cols-2">
        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="flex items-center justify-between">
            <h2 class="font-display text-lg font-semibold">Free models</h2>
            <button class="text-sm font-semibold text-accent" type="button" @click="pickerOpen = true">Add model</button>
          </div>
          <ul class="mt-3 space-y-3">
            <li v-for="pref in freePool" :key="pref.id" class="rounded-md border border-ink-200 p-3 dark:border-ink-700">
              <div class="flex items-start justify-between gap-2">
                <div>
                  <div class="font-medium">{{ pref.name || pref.model_id }}</div>
                  <div class="mt-1 flex flex-wrap gap-1.5 text-xs">
                    <span class="rounded bg-ink-100 px-1.5 py-0.5 font-semibold dark:bg-ink-800">{{ pref.provider_name || pref.provider_id || 'OpenRouter' }}</span>
                    <span class="rounded bg-ink-100 px-1.5 py-0.5 font-semibold dark:bg-ink-800">Priority #{{ pref.priority }}</span>
                    <span
                      v-if="pref.adaptive_rank"
                      class="rounded bg-accent/15 px-1.5 py-0.5 font-semibold text-accent"
                    >Adaptive #{{ pref.adaptive_rank }}</span>
                    <span v-else class="text-ink-500">Adaptive: insufficient data ({{ pref.sample_count || 0 }} samples)</span>
                    <span class="rounded border px-1.5 py-0.5">FREE</span>
                    <span :class="pref.compatible === false ? 'text-red-700' : 'text-emerald-700'">
                      {{ pref.compatibility_reason || 'Compatible' }}
                    </span>
                  </div>
                  <div class="mt-2 text-xs text-ink-600 dark:text-ink-300">
                    Clean success: {{ formatPct(pref.clean_success_rate) }}
                    · Cost: {{ formatUsd(pref.average_cost_per_clean_success_usd) }}
                    · Speed: {{ formatLatency(pref.average_latency_ms) }}
                    · Samples: {{ pref.sample_count || 0 }}
                  </div>
                  <div class="mt-1 text-xs text-ink-500">
                    {{ pref.model_id }} · {{ badge(pref.pricing_tier, pref.stale, pref.unavailable) }}
                    · {{ formatPrice(pref.prompt_price_per_million) }} in / {{ formatPrice(pref.completion_price_per_million) }} out
                  </div>
                </div>
                <div class="flex flex-col gap-1">
                  <button class="text-xs" type="button" @click="move(pref, -1)">Up</button>
                  <button class="text-xs" type="button" @click="move(pref, 1)">Down</button>
                </div>
              </div>
              <div class="mt-2 flex flex-wrap gap-2">
                <button class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="test(pref)">Test</button>
                <button class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="toggle(pref)">{{ pref.enabled ? 'Disable' : 'Enable' }}</button>
                <button class="rounded border border-red-300 px-2 py-1 text-xs text-red-700" type="button" @click="remove(pref)">Remove</button>
              </div>
              <p v-if="testResult[pref.id]" class="mt-1 text-xs text-ink-500">{{ testResult[pref.id] }}</p>
            </li>
            <li v-if="!freePool.length" class="text-sm text-ink-500">No free models configured.</li>
          </ul>
        </section>

        <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="flex items-center justify-between">
            <h2 class="font-display text-lg font-semibold">Paid models</h2>
            <button class="text-sm font-semibold text-accent" type="button" @click="pickerOpen = true">Add model</button>
          </div>
          <ul class="mt-3 space-y-3">
            <li v-for="pref in paidPool" :key="pref.id" class="rounded-md border border-ink-200 p-3 dark:border-ink-700">
              <div class="flex items-start justify-between gap-2">
                <div>
                  <div class="font-medium">{{ pref.name || pref.model_id }}</div>
                  <div class="mt-1 flex flex-wrap gap-1.5 text-xs">
                    <span class="rounded bg-ink-100 px-1.5 py-0.5 font-semibold dark:bg-ink-800">{{ pref.provider_name || pref.provider_id || 'OpenRouter' }}</span>
                    <span class="rounded bg-ink-100 px-1.5 py-0.5 font-semibold dark:bg-ink-800">Priority #{{ pref.priority }}</span>
                    <span
                      v-if="pref.adaptive_rank"
                      class="rounded bg-accent/15 px-1.5 py-0.5 font-semibold text-accent"
                    >Adaptive #{{ pref.adaptive_rank }}</span>
                    <span v-else class="text-ink-500">Adaptive: insufficient data ({{ pref.sample_count || 0 }} samples)</span>
                    <span class="rounded border px-1.5 py-0.5">PAID</span>
                    <span :class="pref.compatible === false ? 'text-red-700' : 'text-emerald-700'">
                      {{ pref.compatibility_reason || 'Compatible' }}
                    </span>
                  </div>
                  <div class="mt-2 text-xs text-ink-600 dark:text-ink-300">
                    Clean success: {{ formatPct(pref.clean_success_rate) }}
                    · Cost: {{ formatUsd(pref.average_cost_per_clean_success_usd) }}
                    · Speed: {{ formatLatency(pref.average_latency_ms) }}
                    · Samples: {{ pref.sample_count || 0 }}
                  </div>
                  <div class="mt-1 text-xs text-ink-500">
                    {{ pref.model_id }} · {{ badge(pref.pricing_tier, pref.stale, pref.unavailable) }}
                    · {{ formatPrice(pref.prompt_price_per_million) }} in / {{ formatPrice(pref.completion_price_per_million) }} out
                  </div>
                </div>
                <div class="flex flex-col gap-1">
                  <button class="text-xs" type="button" @click="move(pref, -1)">Up</button>
                  <button class="text-xs" type="button" @click="move(pref, 1)">Down</button>
                </div>
              </div>
              <div class="mt-2 flex flex-wrap gap-2">
                <button class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="test(pref)">Test</button>
                <button class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="toggle(pref)">{{ pref.enabled ? 'Disable' : 'Enable' }}</button>
                <button class="rounded border border-red-300 px-2 py-1 text-xs text-red-700" type="button" @click="remove(pref)">Remove</button>
              </div>
              <p v-if="testResult[pref.id]" class="mt-1 text-xs text-ink-500">{{ testResult[pref.id] }}</p>
            </li>
            <li v-if="!paidPool.length" class="text-sm text-ink-500">No paid models configured.</li>
          </ul>
        </section>
      </div>

      <div v-if="pickerOpen" class="rounded-xl border border-ink-200 bg-white p-5 dark:border-ink-800 dark:bg-ink-900">
        <div class="flex items-center justify-between">
          <h2 class="font-display text-lg font-semibold">Add model</h2>
          <button class="text-sm" type="button" @click="pickerOpen = false">Close</button>
        </div>
        <input v-model="pickerQuery" class="mt-3 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 text-sm dark:border-ink-600" placeholder="Search models" />
        <div class="mt-2 flex flex-wrap gap-2 text-sm">
          <button v-for="f in ['all', 'compatible', 'free', 'paid']" :key="f" type="button" class="rounded-md px-2 py-1" :class="pickerFilter === f ? 'bg-ink-100 dark:bg-ink-800' : ''" @click="pickerFilter = f as typeof pickerFilter">{{ f }}</button>
        </div>
        <ul class="mt-3 max-h-96 space-y-2 overflow-y-auto">
          <li v-for="model in filteredCatalog" :key="model.id" class="rounded-md border border-ink-200 p-3 text-sm dark:border-ink-700">
            <div class="font-medium">{{ model.name }}</div>
            <div class="text-xs text-ink-500">{{ model.id }} · {{ (model.pricing_tier || 'unknown').toUpperCase() }} · {{ model.context_length || '—' }} ctx</div>
            <div class="text-xs">{{ formatPrice(model.prompt_price_per_million) }} in / {{ formatPrice(model.completion_price_per_million) }} out</div>
            <div class="text-xs" :class="model.compatible === false ? 'text-red-700' : 'text-emerald-700'">{{ model.compatibility_reason }}</div>
            <div class="mt-2 flex gap-2">
              <button class="rounded border px-2 py-1 text-xs disabled:opacity-40" type="button" :disabled="!canAddFree(model)" @click="addModel(model, 'free')">Add to Free</button>
              <button class="rounded border px-2 py-1 text-xs disabled:opacity-40" type="button" :disabled="!canAddPaid(model)" @click="addModel(model, 'paid')">Add to Paid</button>
            </div>
          </li>
        </ul>
      </div>
    </template>
  </div>
</template>
