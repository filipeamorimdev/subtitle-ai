<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { LanguageCatalogItem } from '../types'

const store = useAppStore()
const catalog = ref<LanguageCatalogItem[]>([])
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const saving = ref(false)

const form = reactive({
  source_language_code: 'en',
  target_language_code: 'pt-PT',
  target_language_name: 'Portuguese (Portugal)',
})

const languageOptions = computed(() => {
  const items = catalog.value.map((l) => ({ code: l.code, name: l.display_name }))
  const extra: { code: string; name: string }[] = []
  for (const code of [form.source_language_code, form.target_language_code]) {
    if (code && !items.some((l) => l.code === code) && !extra.some((l) => l.code === code)) {
      extra.push({ code, name: code })
    }
  }
  return [...extra, ...items]
})

onMounted(async () => {
  await store.loadSettings()
  try {
    catalog.value = await api.getLanguages()
  } catch {
    catalog.value = []
  }
  const s = store.settings
  if (!s) return
  form.target_language_code = s.target_language.code
  form.target_language_name = s.target_language.name
  form.source_language_code = s.source_languages?.[0] || 'en'
})

function onTargetChange() {
  const match = languageOptions.value.find((l) => l.code === form.target_language_code)
  if (match) form.target_language_name = match.name
}

async function save() {
  saving.value = true
  message.value = null
  error.value = null
  try {
    onTargetChange()
    await api.updateSettings({
      target_language_code: form.target_language_code,
      target_language_name: form.target_language_name,
      source_languages: [form.source_language_code || 'en'],
    })
    await store.loadSettings()
    message.value = 'Language settings saved.'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="space-y-8">
    <div>
      <h2 class="font-display text-lg font-semibold">Language</h2>
      <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
        Defaults for source matching and new localization requests.
      </p>
    </div>

    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      {{ message }}
    </p>
    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>

    <form class="space-y-8" @submit.prevent="save">
      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Defaults</legend>
        <label class="block text-sm">
          <span class="text-ink-500">Source language</span>
          <select
            v-model="form.source_language_code"
            class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
          >
            <option v-for="lang in languageOptions" :key="`source-${lang.code}`" :value="lang.code">
              {{ lang.name }} ({{ lang.code }})
            </option>
          </select>
          <span class="mt-1 block text-xs text-ink-500">
            Preferred language for source subtitles when requesting, extracting, and matching. Defaults to English.
          </span>
        </label>
        <label class="block text-sm">
          <span class="text-ink-500">Target language</span>
          <select
            v-model="form.target_language_code"
            class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 dark:border-ink-600"
            @change="onTargetChange"
          >
            <option v-for="lang in languageOptions" :key="`target-${lang.code}`" :value="lang.code">
              {{ lang.name }} ({{ lang.code }})
            </option>
          </select>
          <span class="mt-1 block text-xs text-ink-500">
            Used for Bazarr wanted matching and new localize requests.
          </span>
        </label>
      </fieldset>

      <button
        class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
        type="submit"
        :disabled="saving"
      >
        {{ saving ? 'Saving…' : 'Save language' }}
      </button>
    </form>
  </section>
</template>
