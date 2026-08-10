<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import OpenRouterModelSelect from '../components/OpenRouterModelSelect.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { OpenRouterModel, PathMapping } from '../types'

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
const bazarrTest = ref<string | null>(null)
const openrouterTest = ref<string | null>(null)
const openrouterModels = ref<OpenRouterModel[]>([])
const modelsLoading = ref(false)
const modelsError = ref<string | null>(null)

const form = reactive({
  bazarr_url: '',
  bazarr_api_key: '',
  clear_bazarr_api_key: false,
  openrouter_api_key: '',
  clear_openrouter_api_key: false,
  openrouter_model: 'openai/gpt-4o-mini',
  target_language_code: 'pt-PT',
  target_language_name: 'Portuguese (Portugal)',
  source_language_code: 'en',
  media_roots: '/media',
  path_mappings: '' as string,
})

function mappingsToText(mappings: PathMapping[]) {
  return mappings.map((m) => `${m.bazarr_prefix} => ${m.local_prefix}`).join('\n')
}

function textToMappings(text: string): PathMapping[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [bazarr_prefix, local_prefix] = line.split('=>').map((part) => part.trim())
      return { bazarr_prefix, local_prefix }
    })
    .filter((m) => m.bazarr_prefix && m.local_prefix)
}

async function loadOpenRouterModels() {
  modelsLoading.value = true
  modelsError.value = null
  try {
    const result = await api.getOpenRouterModels()
    openrouterModels.value = result.models
  } catch (err) {
    modelsError.value = err instanceof Error ? err.message : String(err)
  } finally {
    modelsLoading.value = false
  }
}

onMounted(async () => {
  await store.loadSettings()
  const s = store.settings
  if (!s) return
  form.bazarr_url = s.bazarr_url || ''
  form.openrouter_model = s.openrouter_model
  form.target_language_code = s.target_language.code
  form.target_language_name = s.target_language.name
  form.source_language_code = s.source_languages?.[0] || 'en'
  form.media_roots = s.media_roots.join(', ')
  form.path_mappings = mappingsToText(s.path_mappings)
  await loadOpenRouterModels()
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
      target_language_code: form.target_language_code,
      target_language_name: form.target_language_name,
      source_languages: [form.source_language_code || 'en'],
      media_roots: form.media_roots.split(',').map((s) => s.trim()).filter(Boolean),
      path_mappings: textToMappings(form.path_mappings),
    })
    form.bazarr_api_key = ''
    form.openrouter_api_key = ''
    form.clear_bazarr_api_key = false
    form.clear_openrouter_api_key = false
    await store.loadSettings()
    message.value = 'Settings saved.'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
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

      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
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
          <div class="flex items-center justify-between gap-3">
            <span class="text-ink-500">Model</span>
            <button
              type="button"
              class="text-xs font-semibold text-ink-500 hover:text-ink-800 dark:hover:text-ink-200"
              :disabled="modelsLoading"
              @click="loadOpenRouterModels"
            >
              {{ modelsLoading ? 'Refreshing…' : 'Refresh list' }}
            </button>
          </div>
          <OpenRouterModelSelect
            v-model="form.openrouter_model"
            :models="openrouterModels"
            :loading="modelsLoading"
            :error="modelsError"
            @refresh="loadOpenRouterModels"
          />
          <span class="mt-1 block text-xs text-ink-500">
            Models are loaded from OpenRouter and sorted by price (cheapest first). Prices are USD per million tokens.
          </span>
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <button class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold dark:border-ink-600" type="button" @click="testOpenRouter">
            Test Connection
          </button>
          <span v-if="openrouterTest" class="min-w-0 break-words text-sm text-ink-600 dark:text-ink-300">{{ openrouterTest }}</span>
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
      </fieldset>

      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Media</legend>
        <label class="block text-sm">
          <span class="text-ink-500">Container media roots (comma-separated)</span>
          <input v-model="form.media_roots" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600" />
        </label>
        <label class="block text-sm">
          <span class="text-ink-500">Path mappings (one per line: Bazarr path => local path)</span>
          <textarea v-model="form.path_mappings" rows="4" class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-ink-600" placeholder="/movies => /media/movies" />
          <span class="mt-1 block text-xs text-ink-500">
            Bazarr and Subtitle AI must see compatible media paths. Map prefixes when they differ.
          </span>
        </label>
      </fieldset>

      <button class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-60" type="submit" :disabled="saving">
        {{ saving ? 'Saving…' : 'Save settings' }}
      </button>
    </form>
  </section>
</template>
