<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { AiProviderInfo } from '../types'

const store = useAppStore()
const bazarrMessage = ref<string | null>(null)
const bazarrError = ref<string | null>(null)
const saving = ref(false)
const bazarrTest = ref<string | null>(null)

const form = reactive({
  bazarr_url: '',
  bazarr_api_key: '',
  clear_bazarr_api_key: false,
})

const providers = ref<AiProviderInfo[]>([])
const aiError = ref<string | null>(null)
const aiMessage = ref<string | null>(null)
const aiLoading = ref(true)
const apiKey = ref('')
const clearApiKey = ref(false)
const testMessage = ref<string | null>(null)
const logExchanges = ref(false)

const upcomingProviders = [
  { id: 'anthropic', name: 'Anthropic' },
  { id: 'openai', name: 'OpenAI' },
]

async function loadAi() {
  aiLoading.value = true
  aiError.value = null
  try {
    const [list, route] = await Promise.all([api.getAiProviders(), api.getAiRouting()])
    providers.value = list.providers
    logExchanges.value = Boolean(route.openrouter_log_full_exchanges)
  } catch (err) {
    aiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    aiLoading.value = false
  }
}

onMounted(async () => {
  await store.loadSettings()
  const s = store.settings
  if (s) form.bazarr_url = s.bazarr_url || ''
  await loadAi()
})

async function saveBazarr() {
  saving.value = true
  bazarrMessage.value = null
  bazarrError.value = null
  try {
    await api.updateSettings({
      bazarr_url: form.bazarr_url,
      bazarr_api_key: form.bazarr_api_key || undefined,
      clear_bazarr_api_key: form.clear_bazarr_api_key,
    })
    form.bazarr_api_key = ''
    form.clear_bazarr_api_key = false
    await store.loadSettings()
    bazarrMessage.value = 'Bazarr settings saved.'
  } catch (err) {
    bazarrError.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

async function testBazarr() {
  bazarrTest.value = null
  const result = await api.testBazarr()
  bazarrTest.value = result.message
}

async function saveOpenRouter() {
  aiMessage.value = null
  aiError.value = null
  try {
    await api.updateAiProvider('openrouter', {
      api_key: apiKey.value || undefined,
      clear_api_key: clearApiKey.value,
      openrouter_log_full_exchanges: logExchanges.value,
    })
    apiKey.value = ''
    clearApiKey.value = false
    aiMessage.value = 'OpenRouter provider saved.'
    await loadAi()
  } catch (err) {
    aiError.value = err instanceof Error ? err.message : String(err)
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

async function refreshModels() {
  aiMessage.value = null
  const result = await api.refreshAiModels('openrouter')
  aiMessage.value = result.ok
    ? `Refreshed ${result.count} models.`
    : result.message || 'Refresh failed; kept last catalog.'
}
</script>

<template>
  <section class="space-y-8">
    <div>
      <h2 class="font-display text-lg font-semibold">Providers</h2>
      <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
        Subtitle sources and LLM accounts.
      </p>
    </div>

    <section class="space-y-4">
      <div>
        <h3 class="font-display text-lg font-semibold">Media</h3>
        <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
          Bazarr is the current library provider.
        </p>
      </div>

      <p
        v-if="bazarrMessage"
        class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800"
      >
        {{ bazarrMessage }}
      </p>
      <p v-if="bazarrError" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
        {{ bazarrError }}
      </p>

      <form class="space-y-4" @submit.prevent="saveBazarr">
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

        <button
          class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
          type="submit"
          :disabled="saving"
        >
          {{ saving ? 'Saving…' : 'Save Bazarr' }}
        </button>
      </form>
    </section>

    <section class="space-y-4">
      <div>
        <h3 class="font-display text-lg font-semibold">AI</h3>
        <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
          LLM accounts. v0.3-alpha1 implements OpenRouter only. Anthropic and OpenAI are reserved for later.
          ChatGPT or Claude subscriptions are not API access.
        </p>
      </div>

      <p v-if="aiError" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
        {{ aiError }}
      </p>
      <p v-if="aiMessage" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        {{ aiMessage }}
      </p>
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
            <RouterLink
              class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
              to="/settings/models"
            >
              Models &amp; Routing
            </RouterLink>
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
            <label class="mt-2 flex items-center gap-2 text-sm">
              <input v-model="clearApiKey" type="checkbox" />
              Clear saved OpenRouter API key
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
              <button
                class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600"
                type="button"
                @click="refreshModels"
              >
                Refresh models
              </button>
              <button
                class="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white"
                type="button"
                @click="saveOpenRouter"
              >
                Save provider
              </button>
            </div>
          </template>
        </div>

        <div
          v-for="upcoming in upcomingProviders.filter((item) => !providers.some((p) => p.provider_id === item.id))"
          :key="upcoming.id"
          class="rounded-xl border border-dashed border-ink-300 bg-white/50 p-5 dark:border-ink-700 dark:bg-ink-900/40"
        >
          <h4 class="font-display text-lg font-semibold">{{ upcoming.name }}</h4>
          <p class="mt-1 text-sm text-ink-500">Not available yet.</p>
        </div>
      </template>
    </section>
  </section>
</template>
