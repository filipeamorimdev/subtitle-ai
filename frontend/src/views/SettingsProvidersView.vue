<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import SettingsPageHeader from '../components/SettingsPageHeader.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { AiProviderInfo } from '../types'

const store = useAppStore()
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const saving = ref(false)
const bazarrTest = ref<string | null>(null)
const jellyfinTest = ref<string | null>(null)

const form = reactive({
  bazarr_url: '',
  bazarr_api_key: '',
  clear_bazarr_api_key: false,
  jellyfin_url: '',
  jellyfin_api_key: '',
  clear_jellyfin_api_key: false,
  asr_provider: 'local_then_openai',
  asr_local_model: 'small',
  openai_api_key: '',
  clear_openai_api_key: false,
})

const providers = ref<AiProviderInfo[]>([])
const aiLoading = ref(true)
const apiKey = ref('')
const clearingApiKey = ref(false)
const testMessage = ref<string | null>(null)
const logExchanges = ref(false)
const temperature = ref(0)

async function loadAi(opts?: { silent?: boolean }) {
  if (!opts?.silent) {
    aiLoading.value = true
    error.value = null
  }
  try {
    const [list, route] = await Promise.all([api.getAiProviders(), api.getAiRouting()])
    providers.value = list.providers
    logExchanges.value = Boolean(route.openrouter_log_full_exchanges)
    temperature.value =
      typeof route.openrouter_temperature === 'number' ? route.openrouter_temperature : 0
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    aiLoading.value = false
  }
}

onMounted(async () => {
  await store.loadSettings()
  const s = store.settings
  if (s) {
    form.bazarr_url = s.bazarr_url || ''
    form.jellyfin_url = s.jellyfin_url || ''
    form.asr_provider = s.asr_provider || 'local_then_openai'
    form.asr_local_model = s.asr_local_model || 'small'
  }
  await loadAi()
})

async function save() {
  saving.value = true
  message.value = null
  error.value = null
  try {
    const results = await Promise.allSettled([
      api.updateSettings({
        bazarr_url: form.bazarr_url,
        bazarr_api_key: form.bazarr_api_key || undefined,
        clear_bazarr_api_key: form.clear_bazarr_api_key,
        jellyfin_url: form.jellyfin_url,
        jellyfin_api_key: form.jellyfin_api_key || undefined,
        clear_jellyfin_api_key: form.clear_jellyfin_api_key,
        asr_provider: form.asr_provider,
        asr_local_model: form.asr_local_model,
        openai_api_key: form.openai_api_key || undefined,
        clear_openai_api_key: form.clear_openai_api_key,
      }),
      api.updateAiProvider('openrouter', {
        api_key: apiKey.value || undefined,
        openrouter_log_full_exchanges: logExchanges.value,
        openrouter_temperature: Number.isFinite(Number(temperature.value))
          ? Math.min(2, Math.max(0, Number(temperature.value)))
          : 0,
      }),
    ])
    const failures = results
      .map((result, index) => {
        if (result.status === 'fulfilled') return null
        const label = index === 0 ? 'Media providers' : 'OpenRouter'
        const reason = result.reason instanceof Error ? result.reason.message : String(result.reason)
        return `${label}: ${reason}`
      })
      .filter((item): item is string => Boolean(item))

    if (results[0].status === 'fulfilled') {
      form.bazarr_api_key = ''
      form.clear_bazarr_api_key = false
      form.jellyfin_api_key = ''
      form.clear_jellyfin_api_key = false
      form.openai_api_key = ''
      form.clear_openai_api_key = false
      await store.loadSettings()
      const s = store.settings
      if (s) {
        form.asr_provider = s.asr_provider || 'local_then_openai'
        form.asr_local_model = s.asr_local_model || 'small'
      }
    }
    if (results[1].status === 'fulfilled') {
      apiKey.value = ''
      await loadAi({ silent: true })
    }

    if (failures.length) {
      error.value = failures.join(' ')
      if (failures.length < results.length) message.value = 'Some provider settings were saved.'
    } else {
      message.value = 'Providers saved.'
    }
  } finally {
    saving.value = false
  }
}

async function testBazarr() {
  bazarrTest.value = null
  const result = await api.testBazarr()
  bazarrTest.value = result.message
}

async function testJellyfin() {
  jellyfinTest.value = null
  const result = await api.testJellyfin()
  jellyfinTest.value = result.message
}

async function clearOpenRouterApiKey() {
  if (!confirm('Remove the saved OpenRouter API key? This cannot be undone.')) return
  clearingApiKey.value = true
  message.value = null
  error.value = null
  try {
    await api.updateAiProvider('openrouter', { clear_api_key: true })
    apiKey.value = ''
    await loadAi({ silent: true })
    message.value = 'Saved OpenRouter API key removed.'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    clearingApiKey.value = false
  }
}

async function testOpenRouter(fresh = false) {
  testMessage.value = 'Testing…'
  try {
    const result = await api.testAiProvider('openrouter', { fresh })
    const cached = result.details && (result.details as { cached?: boolean }).cached
    testMessage.value = `${result.message}${cached ? ' (cached)' : ''}`
    await loadAi()
  } catch (err) {
    testMessage.value = err instanceof Error ? err.message : String(err)
  }
}

</script>

<template>
  <section class="space-y-8">
    <SettingsPageHeader
      title="Providers"
      save-label="Save"
      form="settings-providers-form"
      :saving="saving"
      :disabled="aiLoading"
    />

    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      {{ message }}
    </p>
    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>

    <form id="settings-providers-form" class="space-y-8" @submit.prevent="save">
      <section class="space-y-4">
        <h3 class="font-display text-lg font-semibold">Media</h3>
        <p class="text-sm text-ink-500">
          When Jellyfin is connected, its movies and episodes are used in media search and AI
          requests. If it is unavailable or not configured, the catalog automatically uses Bazarr.
        </p>
        <fieldset
          class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
        >
          <legend class="px-1 font-display text-lg font-semibold">Jellyfin catalog</legend>
          <label class="block text-sm">
            <span class="text-ink-500">URL</span>
            <input
              v-model="form.jellyfin_url"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              placeholder="http://jellyfin:8096"
            />
          </label>
          <label class="block text-sm">
            <span class="text-ink-500">API key</span>
            <input
              v-model="form.jellyfin_api_key"
              type="password"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              placeholder="Leave blank to keep existing"
            />
            <span v-if="store.settings?.jellyfin_api_key_masked" class="mt-1 block break-all text-xs text-ink-500">
              Saved: {{ store.settings.jellyfin_api_key_masked }}
            </span>
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="form.clear_jellyfin_api_key" type="checkbox" />
            Clear saved Jellyfin API key
          </label>
          <div class="flex flex-wrap items-center gap-3">
            <button
              class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
              type="button"
              @click="testJellyfin"
            >
              Test Connection
            </button>
            <span v-if="jellyfinTest" class="min-w-0 break-words text-sm text-ink-600 dark:text-ink-300">
              {{ jellyfinTest }}
            </span>
          </div>
        </fieldset>
        <fieldset
          class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
        >
          <legend class="px-1 font-display text-lg font-semibold">Bazarr</legend>
          <label class="block text-sm">
            <span class="text-ink-500">URL</span>
            <input
              v-model="form.bazarr_url"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              placeholder="http://bazarr:6767"
            />
          </label>
          <label class="block text-sm">
            <span class="text-ink-500">API key</span>
            <input
              v-model="form.bazarr_api_key"
              type="password"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              placeholder="Leave blank to keep existing"
            />
            <span v-if="store.settings?.bazarr_api_key_masked" class="mt-1 block break-all text-xs text-ink-500">
              Saved: {{ store.settings.bazarr_api_key_masked }}
            </span>
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="form.clear_bazarr_api_key" type="checkbox" />
            Clear saved Bazarr API key
          </label>
          <div class="flex flex-wrap items-center gap-3">
            <button
              class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
              type="button"
              @click="testBazarr"
            >
              Test Connection
            </button>
            <span v-if="bazarrTest" class="min-w-0 break-words text-sm text-ink-600 dark:text-ink-300">
              {{ bazarrTest }}
            </span>
          </div>
        </fieldset>
      </section>

      <section class="space-y-4">
        <h3 class="font-display text-lg font-semibold">Speech-to-text</h3>
        <fieldset
          class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
        >
          <legend class="px-1 font-display text-lg font-semibold">Whisper</legend>
          <p class="text-sm text-ink-500">
            Used only when you click Transcribe audio on a media page. Local models download on first
            use (~500MB for small) and are slow on CPU. There is no GPU in the default Docker image.
          </p>
          <fieldset class="space-y-2 text-sm">
            <legend class="text-ink-500">Engine</legend>
            <label class="flex items-center gap-2">
              <input v-model="form.asr_provider" type="radio" value="local" />
              Local faster-whisper
            </label>
            <label class="flex items-center gap-2">
              <input v-model="form.asr_provider" type="radio" value="openai" />
              OpenAI Whisper API
            </label>
            <label class="flex items-center gap-2">
              <input v-model="form.asr_provider" type="radio" value="local_then_openai" />
              Local, then OpenAI if local fails
            </label>
          </fieldset>
          <label class="block text-sm">
            <span class="text-ink-500">Local model</span>
            <select
              v-model="form.asr_local_model"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
            >
              <option value="tiny">tiny</option>
              <option value="base">base</option>
              <option value="small">small (default)</option>
              <option value="medium">medium</option>
              <option value="large-v3">large-v3</option>
              <option value="distil-large-v3">distil-large-v3</option>
            </select>
          </label>
          <label class="block text-sm">
            <span class="text-ink-500">OpenAI API key</span>
            <input
              v-model="form.openai_api_key"
              type="password"
              class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
              placeholder="Leave blank to keep existing"
            />
            <span v-if="store.settings?.openai_api_key_masked" class="mt-1 block break-all text-xs text-ink-500">
              Saved: {{ store.settings.openai_api_key_masked }}
            </span>
          </label>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="form.clear_openai_api_key" type="checkbox" />
            Clear saved OpenAI API key
          </label>
        </fieldset>
      </section>

      <section class="space-y-4">
        <h3 class="font-display text-lg font-semibold">AI</h3>

        <p v-if="aiLoading" class="text-ink-500">Loading providers…</p>

        <template v-else>
          <div
            v-for="provider in providers"
            :key="provider.provider_id"
            class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h4 class="font-display text-lg font-semibold">{{ provider.display_name }}</h4>
                <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
                  <span v-if="provider.configured">● Configured</span>
                  <span v-else>○ Not configured</span>
                  <span v-if="provider.api_key_masked" class="ml-2 break-all text-xs text-ink-500">
                    {{ provider.api_key_masked }}
                  </span>
                </p>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <RouterLink
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
                  to="/settings/models"
                >
                  Models &amp; Routing
                </RouterLink>
              </div>
            </div>

            <template v-if="provider.provider_id === 'openrouter'">
              <label class="mt-4 block text-sm">
                <span class="text-ink-500">API key</span>
                <input
                  v-model="apiKey"
                  type="password"
                  class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
                  placeholder="Leave blank to keep existing"
                />
              </label>
              <div class="mt-2">
                <button
                  type="button"
                  class="rounded-md bg-red-600/90 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
                  :disabled="clearingApiKey || !provider.api_key_masked"
                  @click="clearOpenRouterApiKey"
                >
                  {{ clearingApiKey ? 'Clearing…' : 'Clear saved OpenRouter API key' }}
                </button>
              </div>
              <label class="mt-4 block text-sm">
                <span class="text-ink-500">Temperature</span>
                <input
                  v-model.number="temperature"
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
                />
                <span class="mt-1 block text-xs text-ink-500">
                  Sent on every OpenRouter request. 0 is deterministic; higher values add variation (max 2).
                </span>
              </label>
              <label class="mt-2 flex items-center gap-2 text-sm">
                <input v-model="logExchanges" type="checkbox" />
                Log full OpenRouter exchanges (debug; may include prompts)
              </label>
              <p v-if="testMessage" class="mt-2 text-sm text-ink-600 dark:text-ink-300">{{ testMessage }}</p>
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
                  type="button"
                  @click="testOpenRouter(false)"
                >
                  Test connection
                </button>
                <button
                  class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
                  type="button"
                  @click="testOpenRouter(true)"
                >
                  Test (force refresh)
                </button>
              </div>
            </template>
          </div>
        </template>
      </section>
    </form>
  </section>
</template>
