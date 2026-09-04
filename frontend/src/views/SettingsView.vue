<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import LanguageSelect from '../components/LanguageSelect.vue'
import SettingsPageHeader from '../components/SettingsPageHeader.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { AutomationStatus, LanguageCatalogItem } from '../types'
import { formatDateTime } from '../utils/datetime'

const store = useAppStore()
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const saving = ref(false)
const clearing = ref(false)
const scanning = ref(false)
const automationStatus = ref<AutomationStatus | null>(null)
const catalog = ref<LanguageCatalogItem[]>([])
const catalogError = ref<string | null>(null)
const catalogLoading = ref(false)

const form = reactive({
  max_concurrent_translate: 1,
  max_concurrent_extract: 1,
  max_concurrent_request: 1,
  max_concurrent_transcribe: 1,
  max_concurrent_dub: 1,
  automatic_fallback_enabled: false,
  automatic_scan_interval_minutes: 5,
  bazarr_grace_period_minutes: 10,
  automatic_retry_enabled: true,
  maximum_automatic_retries: 3,
  source_language_code: 'en',
  target_language_code: 'pt-PT',
  target_language_name: 'Portuguese (Portugal)',
})

const languageOptions = computed(() => {
  const items = [...catalog.value]
  for (const code of [form.source_language_code, form.target_language_code]) {
    if (code && !items.some((lang) => lang.code === code)) {
      items.unshift({ code, display_name: code, aliases: [] })
    }
  }
  return items
})

function onTargetChange(code: string = form.target_language_code) {
  const match = languageOptions.value.find((lang) => lang.code === code)
  if (match) form.target_language_name = match.display_name
}

async function loadAutomationStatus() {
  try {
    automationStatus.value = await api.getAutomationStatus()
  } catch {
    automationStatus.value = null
  }
}

onMounted(async () => {
  await store.loadSettings()
  catalogLoading.value = true
  catalogError.value = null
  try {
    catalog.value = await api.getLanguages()
  } catch (err) {
    catalog.value = []
    catalogError.value = err instanceof Error ? err.message : String(err)
  } finally {
    catalogLoading.value = false
  }
  const s = store.settings
  if (!s) return
  form.max_concurrent_translate = s.max_concurrent_translate
  form.max_concurrent_extract = s.max_concurrent_extract
  form.max_concurrent_request = s.max_concurrent_request
  form.max_concurrent_transcribe = s.max_concurrent_transcribe ?? 1
  form.max_concurrent_dub = s.max_concurrent_dub ?? 1
  form.automatic_fallback_enabled = s.automatic_fallback_enabled ?? false
  form.automatic_scan_interval_minutes = s.automatic_scan_interval_minutes ?? 5
  form.bazarr_grace_period_minutes = s.bazarr_grace_period_minutes ?? 10
  form.automatic_retry_enabled = s.automatic_retry_enabled ?? true
  form.maximum_automatic_retries = s.maximum_automatic_retries ?? 3
  form.target_language_code = s.target_language.code
  form.target_language_name = s.target_language.name
  form.source_language_code = s.source_languages?.[0] || 'en'
  await loadAutomationStatus()
})

async function save() {
  saving.value = true
  message.value = null
  error.value = null
  try {
    onTargetChange()
    await api.updateSettings({
      max_concurrent_translate: Number(form.max_concurrent_translate) || 1,
      max_concurrent_extract: Number(form.max_concurrent_extract) || 1,
      max_concurrent_request: Number(form.max_concurrent_request) || 1,
      max_concurrent_transcribe: Number(form.max_concurrent_transcribe) || 1,
      max_concurrent_dub: Number(form.max_concurrent_dub) || 1,
      automatic_fallback_enabled: form.automatic_fallback_enabled,
      automatic_scan_interval_minutes: Number(form.automatic_scan_interval_minutes) || 5,
      bazarr_grace_period_minutes: Number(form.bazarr_grace_period_minutes) || 0,
      automatic_retry_enabled: form.automatic_retry_enabled,
      maximum_automatic_retries: Number(form.maximum_automatic_retries) || 0,
      target_language_code: form.target_language_code,
      target_language_name: form.target_language_name,
      source_languages: [form.source_language_code || 'en'],
    })
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

async function exportSettings() {
  try {
    const payload = await api.exportSettings()
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'subtitle-ai-settings.json'
    link.click()
    URL.revokeObjectURL(url)
    message.value = 'Settings exported (secrets omitted).'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function runClear(action: () => Promise<{ message: string }>, confirmText: string) {
  if (!confirm(confirmText)) return
  clearing.value = true
  message.value = null
  error.value = null
  try {
    const result = await action()
    message.value = result.message
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    clearing.value = false
  }
}

function clearJobs(opts?: {
  job_kind?: 'translate' | 'extract' | 'request' | 'transcribe' | 'dub'
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

function clearUsageStats() {
  return runClear(
    () => api.clearUsageStats(),
    'Clear usage stats (OpenRouter exchange logs and token totals)? Job history will be kept.',
  )
}
</script>

<template>
  <section class="space-y-8">
    <SettingsPageHeader
      title="General"
      save-label="Save settings"
      form="settings-general-form"
      :saving="saving"
    />

    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      {{ message }}
    </p>
    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>

    <form id="settings-general-form" class="space-y-8" @submit.prevent="save">
      <fieldset
        id="language"
        class="min-w-0 space-y-4 overflow-visible rounded-md border border-ink-200 bg-white p-5 dark:border-ink-800 dark:bg-ink-900"
      >
        <legend class="px-1 font-display text-lg font-semibold">Language</legend>
        <div class="block text-sm">
          <span class="text-ink-500">Source language</span>
          <LanguageSelect
            v-model="form.source_language_code"
            :languages="languageOptions"
            :loading="catalogLoading"
            :error="catalogError"
            placeholder="Select source language"
          />
          <span class="mt-1 block text-xs text-ink-500">
            Language Bazarr should search when no local subtitle exists. Local sidecars and embedded tracks in any other language are still translated. Defaults to English.
          </span>
        </div>
        <div class="block text-sm">
          <span class="text-ink-500">Target language</span>
          <LanguageSelect
            v-model="form.target_language_code"
            :languages="languageOptions"
            :loading="catalogLoading"
            :error="catalogError"
            placeholder="Select target language"
            @update:modelValue="onTargetChange"
          />
        </div>
      </fieldset>

      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-md border border-ink-200 bg-white p-5 dark:border-ink-800 dark:bg-ink-900">
        <legend class="px-1 font-display text-lg font-semibold">Automatic Subtitle Fallback</legend>
        <label class="flex items-start gap-2 text-sm">
          <input v-model="form.automatic_fallback_enabled" type="checkbox" class="mt-1" />
          <span>
            <span class="font-medium">Enable automatic fallback</span>
            <span class="mt-1 block text-xs text-ink-500">
              Off by default. When off, translations are done on-demand only.<br>
              When on, new missing items are processed automatically after the grace period and can incur AI costs in case there's the option for paid models active. Check
              <RouterLink class="font-semibold text-accent hover:underline" to="/settings/models">models settings</RouterLink>.
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
        <legend class="px-1 font-display text-lg font-semibold">Job concurrency</legend>
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label class="block text-sm">
            <span class="flex items-center justify-between text-ink-500"><span>Translate</span><output>{{ form.max_concurrent_translate }}</output></span>
            <input
              v-model.number="form.max_concurrent_translate"
              type="range"
              min="1"
              max="10"
              class="mt-2 w-full accent-accent"
            />
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between text-ink-500"><span>Extract</span><output>{{ form.max_concurrent_extract }}</output></span>
            <input
              v-model.number="form.max_concurrent_extract"
              type="range"
              min="1"
              max="10"
              class="mt-2 w-full accent-accent"
            />
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between text-ink-500"><span>Request</span><output>{{ form.max_concurrent_request }}</output></span>
            <input
              v-model.number="form.max_concurrent_request"
              type="range"
              min="1"
              max="10"
              class="mt-2 w-full accent-accent"
            />
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between text-ink-500"><span>Transcribe</span><output>{{ form.max_concurrent_transcribe }}</output></span>
            <input
              v-model.number="form.max_concurrent_transcribe"
              type="range"
              min="1"
              max="10"
              class="mt-2 w-full accent-accent"
            />
          </label>
          <label class="block text-sm">
            <span class="flex items-center justify-between text-ink-500"><span>Dub</span><output>{{ form.max_concurrent_dub }}</output></span>
            <input
              v-model.number="form.max_concurrent_dub"
              type="range"
              min="1"
              max="10"
              class="mt-2 w-full accent-accent"
            />
          </label>
        </div>
        <span class="block text-xs text-ink-500">Each limit accepts 1–10. Changes apply on the next worker poll.</span>
      </fieldset>
    </form>

    <fieldset class="min-w-0 space-y-5 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
      <legend class="px-1 font-display text-lg font-semibold">Advanced</legend>
      <p class="text-sm text-ink-500">
        Irreversible data cleanup. Exchange logging is configured under
        <RouterLink class="font-semibold text-accent hover:underline" to="/settings/providers">Providers</RouterLink>.
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
            @click="clearJobs({ job_kind: 'transcribe' })"
          >
            Clear transcribe
          </button>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold disabled:opacity-50 dark:border-ink-600"
            :disabled="clearing"
            @click="clearJobs({ job_kind: 'dub' })"
          >
            Clear dub
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

      <div class="space-y-3">
        <h2 class="text-sm font-semibold text-ink-700 dark:text-ink-200">Configuration backup</h2>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
            @click="exportSettings"
          >
            Download settings
          </button>
        </div>
        <p class="text-xs text-ink-500">Exports non-secret settings as JSON. API keys are omitted.</p>
      </div>
    </fieldset>
  </section>
</template>
