<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import LanguageSelect from '../components/LanguageSelect.vue'
import SettingsPageHeader from '../components/SettingsPageHeader.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { LanguageCatalogItem } from '../types'

const store = useAppStore()
const catalog = ref<LanguageCatalogItem[]>([])
const catalogError = ref<string | null>(null)
const catalogLoading = ref(false)
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const saving = ref(false)

const form = reactive({
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
  form.target_language_code = s.target_language.code
  form.target_language_name = s.target_language.name
  form.source_language_code = s.source_languages?.[0] || 'en'
})

function onTargetChange(code: string = form.target_language_code) {
  const match = languageOptions.value.find((lang) => lang.code === code)
  if (match) form.target_language_name = match.display_name
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
    <SettingsPageHeader
      title="Language"
      save-label="Save language"
      form="settings-language-form"
      :saving="saving"
    />

    <p v-if="message" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      {{ message }}
    </p>
    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>

    <form id="settings-language-form" class="space-y-8" @submit.prevent="save">
      <fieldset class="min-w-0 space-y-4 overflow-visible rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Defaults</legend>
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
            Preferred language for source subtitles when requesting, extracting, and matching. Defaults to English.
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
          <span class="mt-1 block text-xs text-ink-500">
            Used for Bazarr wanted matching, default to requests and new automatic localize requests.
          </span>
        </div>
      </fieldset>
    </form>
  </section>
</template>
