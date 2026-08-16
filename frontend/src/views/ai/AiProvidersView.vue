<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../../services/api'
import type { AiProviderInfo, AiRouting } from '../../types'

const providers = ref<AiProviderInfo[]>([])
const routing = ref<AiRouting | null>(null)
const error = ref<string | null>(null)
const message = ref<string | null>(null)
const loading = ref(true)
const apiKey = ref('')
const clearApiKey = ref(false)
const testMessage = ref<string | null>(null)
const logExchanges = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    const [list, route] = await Promise.all([api.getAiProviders(), api.getAiRouting()])
    providers.value = list.providers
    routing.value = route
    logExchanges.value = Boolean(route.openrouter_log_full_exchanges)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveOpenRouter() {
  message.value = null
  error.value = null
  try {
    await api.updateAiProvider('openrouter', {
      api_key: apiKey.value || undefined,
      clear_api_key: clearApiKey.value,
      openrouter_log_full_exchanges: logExchanges.value,
    })
    apiKey.value = ''
    clearApiKey.value = false
    message.value = 'OpenRouter provider saved.'
    await load()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function testOpenRouter(fresh = false) {
  testMessage.value = 'Testing…'
  try {
    const result = await api.testAiProvider('openrouter', { fresh })
    const cached = result.details && (result.details as { cached?: boolean }).cached
    testMessage.value = `${result.message}${cached ? ' (cached)' : ''}`
    await load()
  } catch (err) {
    testMessage.value = err instanceof Error ? err.message : String(err)
  }
}

async function refreshModels() {
  message.value = null
  const result = await api.refreshAiModels('openrouter')
  message.value = result.ok
    ? `Refreshed ${result.count} models.`
    : result.message || 'Refresh failed; kept last catalog.'
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">{{ error }}</p>
    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{{ message }}</p>
    <p v-if="loading" class="text-ink-500">Loading providers…</p>

    <template v-else>
      <p class="text-sm text-ink-600 dark:text-ink-300">
        BYOAI providers use your own API keys. v0.3-alpha1 implements OpenRouter only.
        ChatGPT or Claude subscriptions are not API access.
      </p>

      <section
        v-for="provider in providers"
        :key="provider.provider_id"
        class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="font-display text-lg font-semibold">{{ provider.display_name }}</h2>
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
            to="/ai/models"
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
      </section>
    </template>
  </div>
</template>
