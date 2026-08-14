<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { AutomationStatus } from '../types'
import { formatDateTime } from '../utils/datetime'

const LANGUAGES = [
  { code: 'en', name: 'English' },
  { code: 'pt-PT', name: 'Portuguese (Portugal)' },
  { code: 'pt-BR', name: 'Portuguese (Brazil)' },
  { code: 'es', name: 'Spanish' },
  { code: 'fr', name: 'French' },
  { code: 'de', name: 'German' },
  { code: 'it', name: 'Italian' },
]

const store = useAppStore()
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const saving = ref(false)
const clearing = ref(false)
const scanning = ref(false)
const bazarrTest = ref<string | null>(null)
const openrouterTest = ref<string | null>(null)
const automationStatus = ref<AutomationStatus | null>(null)

const form = reactive({
  bazarr_url: '',
  bazarr_api_key: '',
  clear_bazarr_api_key: false,
  openrouter_api_key: '',
  clear_openrouter_api_key: false,
  openrouter_model: 'openai/gpt-4o-mini',
  openrouter_log_full_exchanges: false,
  target_language_code: 'pt-PT',
  target_language_name: 'Portuguese (Portugal)',
  source_language_code: 'en',
  batch_size: 25,
  max_concurrent_translate: 1,
  max_concurrent_extract: 1,
  max_concurrent_request: 1,
  automatic_fallback_enabled: false,
  automatic_scan_interval_minutes: 5,
  bazarr_grace_period_minutes: 10,
  automatic_retry_enabled: true,
  maximum_automatic_retries: 3,
})

async function loadAutomationStatus() {
  try {
    automationStatus.value = await api.getAutomationStatus()
  } catch {
    automationStatus.value = null
  }
}

onMounted(async () => {
  await store.loadSettings()
  const s = store.settings
  if (!s) return
  form.bazarr_url = s.bazarr_url || ''
  form.openrouter_model = s.openrouter_model
  form.openrouter_log_full_exchanges = s.openrouter_log_full_exchanges ?? false
  form.target_language_code = s.target_language.code
  form.target_language_name = s.target_language.name
  form.source_language_code = s.source_languages?.[0] || 'en'
  form.batch_size = s.batch_size
  form.max_concurrent_translate = s.max_concurrent_translate
  form.max_concurrent_extract = s.max_concurrent_extract
  form.max_concurrent_request = s.max_concurrent_request
  form.automatic_fallback_enabled = s.automatic_fallback_enabled ?? false
  form.automatic_scan_interval_minutes = s.automatic_scan_interval_minutes ?? 5
  form.bazarr_grace_period_minutes = s.bazarr_grace_period_minutes ?? 10
  form.automatic_retry_enabled = s.automatic_retry_enabled ?? true
  form.maximum_automatic_retries = s.maximum_automatic_retries ?? 3
  await loadAutomationStatus()
})

const sourceLanguageOptions = computed(() => {
  const code = form.source_language_code
  if (!code || LANGUAGES.some((l) => l.code === code)) return LANGUAGES
  return [{ code, name: code }, ...LANGUAGES]
})

function onTargetChange() {
  const match = LANGUAGES.find((l) => l.code === form.target_language_code)
  if (match) form.target_language_name = match.name
}

async function save() {
  saving.value = true
  message.value = null
  error.value = null
  try {
    await api.updateSettings({
      bazarr_url: form.bazarr_url,
      bazarr_api_key: form.bazarr_api_key || undefined,
      clear_bazarr_api_key: form.clear_bazarr_api_key,
      openrouter_api_key: form.openrouter_api_key || undefined,
      clear_openrouter_api_key: form.clear_openrouter_api_key,
      openrouter_model: form.openrouter_model,
      openrouter_log_full_exchanges: form.openrouter_log_full_exchanges,
      target_language_code: form.target_language_code,
      target_language_name: form.target_language_name,
      source_languages: [form.source_language_code || 'en'],
      batch_size: Number(form.batch_size) || 25,
      max_concurrent_translate: Number(form.max_concurrent_translate) || 1,
      max_concurrent_extract: Number(form.max_concurrent_extract) || 1,
      max_concurrent_request: Number(form.max_concurrent_request) || 1,
      automatic_fallback_enabled: form.automatic_fallback_enabled,
      automatic_scan_interval_minutes: Number(form.automatic_scan_interval_minutes) || 5,
      bazarr_grace_period_minutes: Number(form.bazarr_grace_period_minutes) || 0,
      automatic_retry_enabled: form.automatic_retry_enabled,
      maximum_automatic_retries: Number(form.maximum_automatic_retries) || 0,
    })
    form.bazarr_api_key = ''
    form.openrouter_api_key = ''
    form.clear_bazarr_api_key = false
    form.clear_openrouter_api_key = false
    await store.loadSettings()
    await loadAutomationStatus()
    message.value = 'Settings saved.'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

async function runAutomaticScan() {
  scanning.value = true
  message.value = null
  error.value = null
  try {
    const result = await api.runAutomationScan()
    await loadAutomationStatus()
    if (!result.ok) {
      error.value = result.message || 'Automatic scan did not run.'
      return
    }
    message.value = `Scan finished: ${result.created_count} created, ${result.reused_count} reused, ${result.skipped_count} skipped.`
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    scanning.value = false
  }
}

async function testBazarr() {
  bazarrTest.value = null
  const result = await api.testBazarr()
  bazarrTest.value = result.message
}

async function testOpenRouter() {
  openrouterTest.value = null
  const result = await api.testOpenRouter()
  openrouterTest.value = result.message
}

async function runClear(action: () => Promise<{ message: string }>, confirmText: string) {
  if (!confirm(confirmText)) return
  clearing.value = true
  message.value = null
  error.value = null
  try {
    const result = await action()
    message.value = result.message
    try {
      await store.loadJobs()
    } catch {
      /* ignore refresh errors after clear */
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    clearing.value = false
  }
}

function clearJobs(opts?: {
  job_kind?: 'translate' | 'extract' | 'request'
  status?: 'failed' | 'skipped' | 'cancelled'
}) {
  let label = 'ALL jobs'
  if (opts?.status && opts?.job_kind) label = `${opts.status} ${opts.job_kind} jobs`
  else if (opts?.status) label = `${opts.status} jobs`
  else if (opts?.job_kind) label = `${opts.job_kind} jobs`
  return runClear(
    () => api.clearJobs(opts),
    `Delete ${label} from history? This cannot be undone.`,
  )
}

function clearGlossaries(kind?: 'universe' | 'series' | 'movie') {
  const label = kind ? `${kind} glossaries` : 'ALL glossaries'
  return runClear(
    () => api.clearGlossaries(kind),
    `Delete ${label}? Terms in those scopes will be removed. This cannot be undone.`,
  )
}

function clearUsageStats() {
  return runClear(
    () => api.clearUsageStats(),
    'Clear usage stats (OpenRouter exchange logs and token totals)? Job history will be kept.',
  )
}
</script>

<template>
  <section class="space-y-8">
    <div>
      <h1 class="font-display text-2xl font-bold sm:text-3xl">Settings</h1>
    </div>

    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      {{ message }}
    </p>
    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>

    <form class="space-y-8" @submit.prevent="save">
      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Bazarr</legend>
        <label class="block text-sm">
          <span class="text-ink-500">URL</span>
          <input v-model="form.bazarr_url" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600" placeholder="http://bazarr:6767" />
        </label>
        <label class="block text-sm">
          <span class="text-ink-500">API key</span>
          <input v-model="form.bazarr_api_key" type="password" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600" placeholder="Leave blank to keep existing" />
          <span v-if="store.settings?.bazarr_api_key_masked" class="mt-1 block break-all text-xs text-ink-500">
            Saved: {{ store.settings.bazarr_api_key_masked }}
          </span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.clear_bazarr_api_key" type="checkbox" />
          Clear saved Bazarr API key
        </label>
        <div class="flex flex-wrap items-center gap-3">
          <button class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600" type="button" @click="testBazarr">
            Test Connection
          </button>
          <span v-if="bazarrTest" class="min-w-0 break-words text-sm text-ink-600 dark:text-ink-300">{{ bazarrTest }}</span>
        </div>
      </fieldset>

      <fieldset class="min-w-0 space-y-4 overflow-visible rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">OpenRouter</legend>
        <label class="block text-sm">
          <span class="text-ink-500">API key</span>
          <input v-model="form.openrouter_api_key" type="password" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600" placeholder="Leave blank to keep existing" />
          <span v-if="store.settings?.openrouter_api_key_masked" class="mt-1 block break-all text-xs text-ink-500">
            Saved: {{ store.settings.openrouter_api_key_masked }}
          </span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.clear_openrouter_api_key" type="checkbox" />
          Clear saved OpenRouter API key
        </label>
        <div class="block text-sm">
          <span class="text-ink-500">Models</span>
          <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
            Preferred model: <span class="font-medium">{{ store.settings?.openrouter_model || '—' }}</span>
            · Strategy: {{ store.settings?.routing_strategy || 'free_first' }}
          </p>
          <RouterLink class="mt-2 inline-block text-sm font-semibold text-accent hover:underline" to="/ai/models">
            Manage AI models, routing, and cost controls
          </RouterLink>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <button class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600" type="button" @click="testOpenRouter">
            Test Connection
          </button>
          <span v-if="openrouterTest" class="min-w-0 break-words text-sm text-ink-600 dark:text-ink-300">{{ openrouterTest }}</span>
        </div>
        <label class="flex items-start gap-2 text-sm">
          <input v-model="form.openrouter_log_full_exchanges" type="checkbox" class="mt-1" />
          <span>
            <span class="font-medium">Log full OpenRouter exchanges</span>
            <span class="mt-1 block text-xs text-ink-500">
              Off by default. When enabled, job logs include full request/response subtitle content for debugging.
            </span>
          </span>
        </label>
      </fieldset>

      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Automatic Subtitle Fallback</legend>
        <label class="flex items-start gap-2 text-sm">
          <input v-model="form.automatic_fallback_enabled" type="checkbox" class="mt-1" />
          <span>
            <span class="font-medium">Enable automatic fallback</span>
            <span class="mt-1 block text-xs text-ink-500">
              Off by default. When off, Request / Extract / Translate stay click-only. When on, new missing items are processed automatically after the grace period and can incur OpenRouter costs.
            </span>
          </span>
        </label>
        <div class="grid gap-4 sm:grid-cols-3">
          <label class="block text-sm">
            <span class="text-ink-500">Scan interval (minutes)</span>
            <input
              v-model.number="form.automatic_scan_interval_minutes"
              type="number"
              min="1"
              max="1440"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              :disabled="!form.automatic_fallback_enabled"
            />
          </label>
          <label class="block text-sm">
            <span class="text-ink-500">Bazarr grace period (minutes)</span>
            <input
              v-model.number="form.bazarr_grace_period_minutes"
              type="number"
              min="0"
              max="1440"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              :disabled="!form.automatic_fallback_enabled"
            />
          </label>
          <label class="block text-sm">
            <span class="text-ink-500">Automatic retries</span>
            <input
              v-model.number="form.maximum_automatic_retries"
              type="number"
              min="0"
              max="20"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              :disabled="!form.automatic_fallback_enabled || !form.automatic_retry_enabled"
            />
          </label>
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.automatic_retry_enabled" type="checkbox" :disabled="!form.automatic_fallback_enabled" />
          Retry temporary automatic failures
        </label>
        <div class="flex flex-wrap items-center gap-3">
          <button
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            type="button"
            :disabled="!form.automatic_fallback_enabled || scanning"
            @click="runAutomaticScan"
          >
            {{ scanning ? 'Scanning…' : 'Run automatic scan now' }}
          </button>
          <span v-if="automationStatus" class="min-w-0 break-words text-xs text-ink-500">
            Last scan: {{ formatDateTime(automationStatus.last_scan_at) || 'never' }}
            <template v-if="automationStatus.next_scan_at">
              · Next: {{ formatDateTime(automationStatus.next_scan_at) }}
            </template>
          </span>
        </div>
      </fieldset>

      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Translation</legend>
        <label class="block text-sm">
          <span class="text-ink-500">Source language</span>
          <select v-model="form.source_language_code" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600">
            <option v-for="lang in sourceLanguageOptions" :key="`source-${lang.code}`" :value="lang.code">{{ lang.name }} ({{ lang.code }})</option>
          </select>
          <span class="mt-1 block text-xs text-ink-500">
            Preferred language for source subtitles when requesting, extracting, and matching. Defaults to English.
          </span>
        </label>
        <label class="block text-sm">
          <span class="text-ink-500">Target language</span>
          <select v-model="form.target_language_code" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600" @change="onTargetChange">
            <option v-for="lang in LANGUAGES" :key="`target-${lang.code}`" :value="lang.code">{{ lang.name }} ({{ lang.code }})</option>
          </select>
        </label>
        <label class="block text-sm">
          <span class="text-ink-500">Batch size</span>
          <input
            v-model.number="form.batch_size"
            type="number"
            min="1"
            max="200"
            class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
          />
          <span class="mt-1 block text-xs text-ink-500">
            Number of subtitle blocks sent per OpenRouter request during translation (1–200).
          </span>
        </label>
      </fieldset>

      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Job concurrency</legend>
        <p class="text-sm text-ink-500">
          How many jobs of each type can run at the same time. Defaults are 1 per type (one translate, one extract, and one request in parallel).
        </p>
        <div class="grid gap-4 sm:grid-cols-3">
          <label class="block text-sm">
            <span class="text-ink-500">Translate</span>
            <input
              v-model.number="form.max_concurrent_translate"
              type="number"
              min="1"
              max="20"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
            />
          </label>
          <label class="block text-sm">
            <span class="text-ink-500">Extract</span>
            <input
              v-model.number="form.max_concurrent_extract"
              type="number"
              min="1"
              max="20"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
            />
          </label>
          <label class="block text-sm">
            <span class="text-ink-500">Request</span>
            <input
              v-model.number="form.max_concurrent_request"
              type="number"
              min="1"
              max="20"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
            />
          </label>
        </div>
        <span class="block text-xs text-ink-500">Each limit accepts 1–20. Changes apply on the next worker poll.</span>
      </fieldset>

      <button class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-60" type="submit" :disabled="saving">
        {{ saving ? 'Saving…' : 'Save settings' }}
      </button>
    </form>

    <fieldset class="min-w-0 space-y-5 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
      <legend class="px-1 font-display text-lg font-semibold">Clear data</legend>
      <p class="text-sm text-ink-500">
        Permanently delete stored history and stats. These actions cannot be undone.
      </p>

      <div class="space-y-3">
        <h2 class="text-sm font-semibold text-ink-700 dark:text-ink-200">Jobs history</h2>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearJobs({ job_kind: 'translate' })"
          >
            Clear translate
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearJobs({ job_kind: 'extract' })"
          >
            Clear extract
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearJobs({ job_kind: 'request' })"
          >
            Clear request
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearJobs({ status: 'failed' })"
          >
            Clear failed
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearJobs({ status: 'skipped' })"
          >
            Clear skipped
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearJobs({ status: 'cancelled' })"
          >
            Clear cancelled
          </button>
          <button
            type="button"
            class="rounded-md bg-red-600/90 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
            :disabled="clearing"
            @click="clearJobs()"
          >
            Clear all jobs
          </button>
        </div>
      </div>

      <div class="space-y-3">
        <h2 class="text-sm font-semibold text-ink-700 dark:text-ink-200">Glossaries</h2>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearGlossaries('universe')"
          >
            Clear universes
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearGlossaries('series')"
          >
            Clear series
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearGlossaries('movie')"
          >
            Clear movies
          </button>
          <button
            type="button"
            class="rounded-md bg-red-600/90 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
            :disabled="clearing"
            @click="clearGlossaries()"
          >
            Clear all glossaries
          </button>
        </div>
      </div>

      <div class="space-y-3">
        <h2 class="text-sm font-semibold text-ink-700 dark:text-ink-200">Usage stats</h2>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="rounded-md bg-red-600/90 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
            :disabled="clearing"
            @click="clearUsageStats"
          >
            Clear usage stats
          </button>
        </div>
        <p class="text-xs text-ink-500">
          Removes OpenRouter exchange logs and resets token totals. Job history rows are kept.
        </p>
      </div>
    </fieldset>
  </section>
</template>
