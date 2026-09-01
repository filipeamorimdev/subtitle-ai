<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import SettingsPageHeader from '../../components/SettingsPageHeader.vue'
import { api } from '../../services/api'
import type { AiModelsPayload, AiPreference, OpenRouterModel } from '../../types'

const data = ref<AiModelsPayload | null>(null)
const error = ref<string | null>(null)
const message = ref<string | null>(null)
const loading = ref(true)
const pickerOpen = ref(false)
const pickerQuery = ref('')
const pickerError = ref<string | null>(null)
const pickerFilter = ref<'all' | 'compatible' | 'free' | 'paid' | 'audio'>('compatible')
const pickerPurpose = ref<PickerPurpose>('translation')
const testResult = ref<Record<number, string>>({})
const batchSize = ref(25)
const operatorModelId = ref<string>('')
const saving = ref(false)
const catalogRefreshing = ref(false)

const CATALOG_PAGE_CACHE_SECONDS = 5 * 60

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

type ModelTier = 'free' | 'paid'
type ModelPurpose = 'translation' | 'audio_analysis'
type PickerPurpose = ModelPurpose | 'operator'

type ModelPool = {
  purpose: ModelPurpose
  tier: ModelTier | null
  title: string
  empty: string
  badge: string
  items: AiPreference[]
}

const freePool = computed(() => poolItems('translation', 'free'))
const paidPool = computed(() => poolItems('translation', 'paid'))
const audioPool = computed(() => poolItems('audio_analysis'))
const toolModels = computed(() =>
  (data.value?.catalog || []).filter(
    (m: OpenRouterModel) =>
      Array.isArray(m.capabilities) &&
      m.capabilities.includes('function_calling') &&
      m.unavailable !== true,
  ),
)
const modelPools = computed<ModelPool[]>(() => [
  {
    purpose: 'translation',
    tier: 'free',
    title: 'Free models',
    empty: 'No free models configured.',
    badge: 'FREE',
    items: freePool.value,
  },
  {
    purpose: 'translation',
    tier: 'paid',
    title: 'Paid models',
    empty: 'No paid models configured.',
    badge: 'PAID',
    items: paidPool.value,
  },
  {
    purpose: 'audio_analysis',
    tier: null,
    title: 'Audio analysis models',
    empty: 'No audio analysis models configured.',
    badge: 'AUDIO',
    items: audioPool.value,
  },
])

const drag = ref<{ pool: ModelPool; id: number; originalIds: number[] } | null>(null)
const dropAt = ref<{ pool: ModelPool; index: number } | null>(null)

function poolItems(purpose: ModelPurpose, tier: ModelTier | null = null): AiPreference[] {
  return (data.value?.preferences || [])
    .filter((p) => (p.purpose || 'translation') === purpose && (purpose === 'audio_analysis' || p.tier === tier))
    .sort((a, b) => a.priority - b.priority)
}

function samePool(a: ModelPool, b: ModelPool) {
  return a.purpose === b.purpose && a.tier === b.tier
}

function selectedForPurpose(modelId: string, purpose: ModelPurpose) {
  return (data.value?.preferences || []).some(
    (pref) => pref.model_id === modelId && (pref.purpose || 'translation') === purpose,
  )
}

const filteredCatalog = computed(() => {
  const models = data.value?.catalog || []
  const q = pickerQuery.value.trim().toLowerCase()
  return models.filter((m) => {
    if (pickerPurpose.value === 'operator') {
      if (!Array.isArray(m.capabilities) || !m.capabilities.includes('function_calling') || m.unavailable === true) return false
    } else if (selectedForPurpose(m.id, pickerPurpose.value)) return false
    if (pickerFilter.value === 'compatible' && m.compatible === false) return false
    if (pickerFilter.value === 'free' && m.pricing_tier !== 'free') return false
    if (pickerFilter.value === 'paid' && m.pricing_tier !== 'paid') return false
    if (pickerFilter.value === 'audio' && m.audio_analysis_compatible !== true) return false
    if (!q) return true
    return m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)
  })
})

async function load() {
  const showLoading = data.value == null
  if (showLoading) loading.value = true
  error.value = null
  try {
    data.value = await api.getAiModels()
    Object.assign(routing, data.value.routing)
    try {
      const settings = await api.getSettings()
      batchSize.value = settings.batch_size || 25
      operatorModelId.value = settings.operator_model_id || ''
    } catch {
      /* keep default */
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveRouting() {
  saving.value = true
  message.value = null
  error.value = null
  try {
    await api.updateAiRouting({
      ...routing,
      clear_maximum_cost_per_job: routing.maximum_cost_per_job_usd == null,
      clear_monthly_budget_amount: routing.monthly_budget_amount_usd == null,
    })
    await api.updateSettings({
      batch_size: Number(batchSize.value) || 25,
      operator_model_id: operatorModelId.value || null,
      clear_operator_model_id: !operatorModelId.value,
    })
    message.value = 'Routing, cost controls, chat model, and batch size saved.'
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

function catalogNeedsRefresh(payload: AiModelsPayload | null): boolean {
  if (!payload?.openrouter_configured) return false
  if (payload.catalog_stale) return true
  if (payload.catalog_age_seconds == null) return true
  return payload.catalog_age_seconds >= CATALOG_PAGE_CACHE_SECONDS
}

async function refreshCatalog(options: { force?: boolean; notify?: boolean } = {}) {
  const { force = false, notify = false } = options
  if (catalogRefreshing.value) return
  if (!force && !catalogNeedsRefresh(data.value)) return
  catalogRefreshing.value = true
  if (notify) message.value = null
  try {
    const result = await api.refreshAiModels()
    if (notify) {
      message.value = result.ok
        ? `Refreshed ${result.count} models.`
        : (result.message || 'Refresh failed; kept last catalog.')
    }
    if (result.ok) await load()
  } catch (err) {
    if (notify) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    catalogRefreshing.value = false
  }
}

async function refresh() {
  await refreshCatalog({ force: true, notify: true })
}

function openPicker(purpose: PickerPurpose) {
  pickerQuery.value = ''
  pickerError.value = null
  pickerPurpose.value = purpose
  pickerFilter.value = purpose === 'audio_analysis' ? 'audio' : 'compatible'
  pickerOpen.value = true
}

function closePicker() {
  pickerOpen.value = false
  pickerError.value = null
}

async function addModel(model: OpenRouterModel, tier: ModelTier) {
  pickerError.value = null
  try {
    await api.addAiModel(model.id, tier, 'translation')
    closePicker()
    await load()
  } catch (err) {
    pickerError.value = err instanceof Error ? err.message : String(err)
  }
}

async function addAudioModel(model: OpenRouterModel) {
  pickerError.value = null
  try {
    await api.addAiModel(model.id, undefined, 'audio_analysis')
    closePicker()
    await load()
  } catch (err) {
    pickerError.value = err instanceof Error ? err.message : String(err)
  }
}

function selectOperatorModel(model: OpenRouterModel) {
  operatorModelId.value = model.id
  closePicker()
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

function applyLocalOrder(pool: ModelPool, orderedIds: number[]) {
  if (!data.value) return
  const rank = new Map(orderedIds.map((id, i) => [id, i + 1]))
  for (const pref of data.value.preferences) {
    const next = rank.get(pref.id)
    if (
      (pref.purpose || 'translation') === pool.purpose &&
      (pool.purpose === 'audio_analysis' || pref.tier === pool.tier) &&
      next != null
    ) {
      pref.priority = next
    }
  }
}

function onDragStart(event: DragEvent, pool: ModelPool, pref: AiPreference) {
  const target = event.target as HTMLElement | null
  if (target?.closest('button')) {
    event.preventDefault()
    return
  }
  const transfer = event.dataTransfer
  if (!transfer) return
  transfer.effectAllowed = 'move'
  transfer.setData('text/plain', String(pref.id))
  drag.value = { pool, id: pref.id, originalIds: pool.items.map((item) => item.id) }
  dropAt.value = { pool, index: pool.items.findIndex((item) => item.id === pref.id) }
}

function onDragOverCard(event: DragEvent, pool: ModelPool, index: number) {
  event.preventDefault()
  event.stopPropagation()
  if (!drag.value || !samePool(drag.value.pool, pool)) return
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  const el = event.currentTarget as HTMLElement
  const rect = el.getBoundingClientRect()
  const insert = event.clientY > rect.top + rect.height / 2 ? index + 1 : index
  if (!dropAt.value || !samePool(dropAt.value.pool, pool) || dropAt.value.index !== insert) {
    dropAt.value = { pool, index: insert }
  }
}

function onDragOverList(event: DragEvent, pool: ModelPool) {
  event.preventDefault()
  if (!drag.value || !samePool(drag.value.pool, pool)) return
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  if (!dropAt.value || !samePool(dropAt.value.pool, pool)) {
    dropAt.value = { pool, index: pool.items.length }
  }
}

function showDropSlot(pool: ModelPool, index: number) {
  if (!drag.value || !dropAt.value) return false
  if (!samePool(drag.value.pool, pool) || !samePool(dropAt.value.pool, pool)) return false
  const from = drag.value.originalIds.indexOf(drag.value.id)
  if (index === from || index === from + 1) return false
  return dropAt.value.index === index
}

function onDragEnd() {
  drag.value = null
  dropAt.value = null
}

async function onDrop(event: DragEvent, pool: ModelPool) {
  event.preventDefault()
  event.stopPropagation()
  const state = drag.value
  if (!state || !samePool(state.pool, pool)) {
    onDragEnd()
    return
  }
  const insertAt = dropAt.value && samePool(dropAt.value.pool, pool)
    ? dropAt.value.index
    : state.originalIds.indexOf(state.id)
  const from = state.originalIds.indexOf(state.id)
  onDragEnd()
  if (from < 0 || insertAt < 0) return
  let to = insertAt
  if (from < to) to -= 1
  if (from === to) return
  const ids = [...state.originalIds]
  const [item] = ids.splice(from, 1)
  ids.splice(to, 0, item)
  applyLocalOrder(pool, ids)
  try {
    await api.reorderAiModels(pool.tier, ids, pool.purpose)
  } catch (err) {
    applyLocalOrder(pool, state.originalIds)
    error.value = err instanceof Error ? err.message : String(err)
  }
}

function catalogAge(seconds: number | null): string {
  if (seconds == null) return 'never'
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  return `${Math.round(seconds / 3600)} hours ago`
}

function canAddFree(model: OpenRouterModel) {
  return !selectedForPurpose(model.id, 'translation') && model.compatible !== false && model.pricing_tier === 'free'
}

function canAddPaid(model: OpenRouterModel) {
  return !selectedForPurpose(model.id, 'translation') && model.compatible !== false && (model.pricing_tier === 'paid' || model.pricing_tier === 'unknown')
}

function canAddAudio(model: OpenRouterModel) {
  return !selectedForPurpose(model.id, 'audio_analysis') && model.audio_analysis_compatible === true
}

onMounted(async () => {
  await load()
  await refreshCatalog()
})
</script>

<template>
  <div class="space-y-6">
    <SettingsPageHeader
      title="Models"
      save-label="Save routing"
      save-type="button"
      :saving="saving"
      :disabled="loading || !data"
      @save="saveRouting"
    >
      <template #actions>
        <RouterLink
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          to="/settings/providers"
        >
          Manage providers
        </RouterLink>
        <button
          class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
          type="button"
          :disabled="loading || catalogRefreshing"
          @click="refresh"
        >
          Refresh models
        </button>
      </template>
    </SettingsPageHeader>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">{{ error }}</p>
    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{{ message }}</p>
    <p v-if="loading" class="text-ink-500">Loading models…</p>

    <template v-else-if="data">
      <section
        v-if="!data.openrouter_configured"
        class="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-100"
      >
        OpenRouter is not configured.
        <RouterLink class="font-semibold underline" to="/settings/providers">Configure providers</RouterLink>
        before translating.
      </section>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-semibold">Catalog</h2>
        <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
          <strong>Connection</strong> {{ data.openrouter_configured ? '● Configured' : 'Not configured' }}
          · <strong>Updated</strong> {{ catalogAge(data.catalog_age_seconds) }}
          <span v-if="catalogRefreshing" class="text-ink-500"> (updating…)</span>
          <span v-else-if="data.catalog_stale || data.pricing_freshness === 'stale'" class="text-amber-700">
            (stale pricing)
          </span>
        </p>
      </section>

      <section class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <h2 class="font-display text-lg font-semibold">Chat model</h2>
        <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
          Used by the dashboard operator chat. Prefer a model that supports tool / function calling.
        </p>
        <div class="mt-3">
          <span class="text-sm text-ink-500">Operator model</span>
          <div class="mt-1 flex flex-wrap items-center justify-between gap-3 rounded-md border border-ink-300 p-3 dark:border-ink-600">
            <div class="min-w-0 text-sm">
              <div class="font-medium">
                {{ toolModels.find((model) => model.id === operatorModelId)?.name || (operatorModelId || 'Auto') }}
              </div>
              <div class="truncate text-xs text-ink-500">
                {{ operatorModelId || 'Automatically uses the first tool-capable pool model.' }}
              </div>
            </div>
            <div class="flex gap-2">
              <button class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="openPicker('operator')">
                Choose model
              </button>
              <button v-if="operatorModelId" class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="operatorModelId = ''">
                Use Auto
              </button>
            </div>
          </div>
        </div>
        <p v-if="!toolModels.length" class="mt-2 text-xs text-amber-700 dark:text-amber-300">
          No catalog models advertise function_calling yet — refresh the catalog, or pick Auto and
          ensure a capable model is in your pools.
        </p>
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
          <label class="text-sm">
            <span class="text-ink-500">Batch size</span>
            <input
              v-model.number="batchSize"
              type="number"
              min="1"
              max="200"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
            />
            <span class="mt-1 block text-xs text-ink-500">
              Subtitle blocks per translation request (1–200).
            </span>
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

      <div class="space-y-4">
        <section
          v-for="pool in modelPools"
          :key="`${pool.purpose}:${pool.tier || 'all'}`"
          class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
        >
          <div class="flex items-center justify-between">
            <h2 class="font-display text-lg font-semibold">{{ pool.title }}</h2>
            <button class="text-sm font-semibold text-accent" type="button" @click="openPicker(pool.purpose)">Add model</button>
          </div>
          <p v-if="pool.purpose === 'audio_analysis'" class="mt-1 text-sm text-ink-600 dark:text-ink-300">
            Used for AI analysis of source dialogue before a dub is created. It does not affect translation routing.
          </p>
          <ul
            class="mt-3 flex flex-col gap-3"
            @dragover="onDragOverList($event, pool)"
            @drop="onDrop($event, pool)"
          >
            <li
              v-for="(pref, index) in pool.items"
              :key="pref.id"
              class="contents"
            >
              <div v-show="showDropSlot(pool, index)" class="h-1.5 rounded-full bg-accent" aria-hidden="true" />
              <div
                class="cursor-grab rounded-md border border-ink-200 p-3 select-none active:cursor-grabbing dark:border-ink-700"
                :class="drag?.id === pref.id ? 'opacity-40' : ''"
                draggable="true"
                @dragstart="onDragStart($event, pool, pref)"
                @dragover="onDragOverCard($event, pool, index)"
                @drop="onDrop($event, pool)"
                @dragend="onDragEnd"
              >
                <div class="flex items-start gap-2">
                  <span class="mt-1 text-ink-400" aria-hidden="true" title="Drag to reorder">
                    <svg class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                      <path d="M7 4a1.25 1.25 0 1 1-2.5 0A1.25 1.25 0 0 1 7 4Zm8.5 0a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0ZM7 10a1.25 1.25 0 1 1-2.5 0A1.25 1.25 0 0 1 7 10Zm8.5 0a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0ZM7 16a1.25 1.25 0 1 1-2.5 0A1.25 1.25 0 0 1 7 16Zm8.5 0a1.25 1.25 0 1 1-2.5 0 1.25 1.25 0 0 1 2.5 0Z" />
                    </svg>
                  </span>
                  <div class="min-w-0 flex-1">
                    <div class="font-medium">{{ pref.name || pref.model_id }}</div>
                    <div class="mt-1 flex flex-wrap gap-1.5 text-xs">
                      <span class="rounded bg-ink-100 px-1.5 py-0.5 font-semibold dark:bg-ink-800">{{ pref.provider_name || pref.provider_id || 'OpenRouter' }}</span>
                      <span class="rounded bg-ink-100 px-1.5 py-0.5 font-semibold dark:bg-ink-800">Priority #{{ pref.priority }}</span>
                      <span
                        v-if="pref.adaptive_rank"
                        class="rounded bg-accent/15 px-1.5 py-0.5 font-semibold text-accent"
                      >Adaptive #{{ pref.adaptive_rank }}</span>
                      <span v-else class="text-ink-500">Adaptive: insufficient data ({{ pref.sample_count || 0 }} samples)</span>
                      <span class="rounded border px-1.5 py-0.5">{{ pool.badge }}</span>
                      <span :class="pool.purpose === 'audio_analysis' && pref.audio_analysis_compatible === false ? 'text-red-700' : pool.purpose === 'translation' && pref.compatible === false ? 'text-red-700' : 'text-emerald-700'">
                        {{ pool.purpose === 'audio_analysis' ? pref.audio_analysis_compatibility_reason || 'Audio compatible' : pref.compatibility_reason || 'Compatible' }}
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
                    <div class="mt-2 flex flex-wrap gap-2">
                      <button class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="test(pref)">Test</button>
                      <button class="rounded border border-ink-300 px-2 py-1 text-xs dark:border-ink-600" type="button" @click="toggle(pref)">{{ pref.enabled ? 'Disable' : 'Enable' }}</button>
                      <button class="rounded border border-red-300 px-2 py-1 text-xs text-red-700" type="button" @click="remove(pref)">Remove</button>
                    </div>
                    <p v-if="testResult[pref.id]" class="mt-1 text-xs text-ink-500">{{ testResult[pref.id] }}</p>
                  </div>
                </div>
              </div>
            </li>
            <li v-show="showDropSlot(pool, pool.items.length)" class="h-1.5 rounded-full bg-accent" aria-hidden="true" />
            <li v-if="!pool.items.length" class="text-sm text-ink-500">{{ pool.empty }}</li>
          </ul>
        </section>
      </div>

      <div
        v-if="pickerOpen"
        class="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/50 p-4 sm:items-center"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-model-title"
        @click.self="closePicker"
      >
        <div
          class="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-xl border border-ink-200 bg-white p-5 shadow-xl dark:border-ink-700 dark:bg-ink-900"
        >
          <div class="flex items-start justify-between gap-3">
            <div>
              <h2 id="add-model-title" class="font-display text-lg font-semibold">
                {{ pickerPurpose === 'operator' ? 'Select Operator model' : `Add ${pickerPurpose === 'audio_analysis' ? 'audio analysis model' : 'model'}` }}
              </h2>
              <p v-if="pickerPurpose === 'audio_analysis'" class="mt-1 text-sm text-ink-500">
                Only models that accept audio input can be added to this pool.
              </p>
              <p v-else-if="pickerPurpose === 'operator'" class="mt-1 text-sm text-ink-500">
                Choose a model that supports tool / function calling for the dashboard operator chat.
              </p>
            </div>
            <button
              class="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
              type="button"
              @click="closePicker"
            >
              Close
            </button>
          </div>
          <input
            v-model="pickerQuery"
            class="mt-3 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 text-sm dark:border-ink-600"
            placeholder="Search models"
          />
          <div class="mt-2 flex flex-wrap gap-2 text-sm">
            <button
              v-for="f in ['all', 'compatible', 'free', 'paid', 'audio']"
              :key="f"
              type="button"
              class="rounded-md px-2 py-1"
              :class="pickerFilter === f ? 'bg-ink-100 dark:bg-ink-800' : ''"
              @click="pickerFilter = f as 'all' | 'compatible' | 'free' | 'paid' | 'audio'"
            >
              {{ f }}
            </button>
          </div>
          <p v-if="pickerError" class="mt-3 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
            {{ pickerError }}
          </p>
          <ul class="mt-3 min-h-0 flex-1 space-y-2 overflow-y-auto">
            <li v-if="!filteredCatalog.length" class="text-sm text-ink-500">No matching models.</li>
            <li
              v-for="model in filteredCatalog"
              :key="model.id"
              class="rounded-md border border-ink-200 p-3 text-sm dark:border-ink-700"
            >
              <div class="font-medium">{{ model.name }}</div>
              <div class="text-xs text-ink-500">
                {{ model.id }} · {{ (model.pricing_tier || 'unknown').toUpperCase() }} · {{ model.context_length || '—' }} ctx
              </div>
              <div class="text-xs">
                {{ formatPrice(model.prompt_price_per_million) }} in /
                {{ formatPrice(model.completion_price_per_million) }} out
              </div>
              <div class="text-xs" :class="model.compatible === false ? 'text-red-700' : 'text-emerald-700'">
                {{ model.compatibility_reason }}
              </div>
              <div class="text-xs" :class="model.audio_analysis_compatible === true ? 'text-emerald-700' : 'text-ink-500'">
                {{ model.audio_analysis_compatibility_reason }}
              </div>
              <div class="mt-2 flex gap-2">
                <button
                  v-if="pickerPurpose === 'translation'"
                  class="rounded border px-2 py-1 text-xs disabled:opacity-40"
                  type="button"
                  :disabled="!canAddFree(model)"
                  @click="addModel(model, 'free')"
                >
                  Add to Free
                </button>
                <button
                  v-if="pickerPurpose === 'translation'"
                  class="rounded border px-2 py-1 text-xs disabled:opacity-40"
                  type="button"
                  :disabled="!canAddPaid(model)"
                  @click="addModel(model, 'paid')"
                >
                  Add to Paid
                </button>
                <button
                  v-if="pickerPurpose === 'audio_analysis'"
                  class="rounded border px-2 py-1 text-xs disabled:opacity-40"
                  type="button"
                  :disabled="!canAddAudio(model)"
                  @click="addAudioModel(model)"
                >
                  Add to Audio analysis
                </button>
                <button
                  v-if="pickerPurpose === 'operator'"
                  class="rounded border px-2 py-1 text-xs"
                  type="button"
                  @click="selectOperatorModel(model)"
                >
                  Use for Operator
                </button>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </template>
  </div>
</template>
