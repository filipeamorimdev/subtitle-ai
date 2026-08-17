<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { PathMapping } from '../types'

const store = useAppStore()
const message = ref<string | null>(null)
const error = ref<string | null>(null)
const saving = ref(false)
const bazarrTest = ref<string | null>(null)

const form = reactive({
  bazarr_url: '',
  bazarr_api_key: '',
  clear_bazarr_api_key: false,
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

onMounted(async () => {
  await store.loadSettings()
  const s = store.settings
  if (!s) return
  form.bazarr_url = s.bazarr_url || ''
  form.path_mappings = mappingsToText(s.path_mappings || [])
})

async function save() {
  saving.value = true
  message.value = null
  error.value = null
  try {
    await api.updateSettings({
      bazarr_url: form.bazarr_url,
      bazarr_api_key: form.bazarr_api_key || undefined,
      clear_bazarr_api_key: form.clear_bazarr_api_key,
      path_mappings: textToMappings(form.path_mappings),
    })
    form.bazarr_api_key = ''
    form.clear_bazarr_api_key = false
    await store.loadSettings()
    message.value = 'Provider settings saved.'
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
</script>

<template>
  <section class="space-y-8">
    <div>
      <h2 class="font-display text-lg font-semibold">Providers</h2>
      <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
        Subtitle and media sources. Bazarr is the current library provider.
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

      <fieldset class="min-w-0 space-y-4 overflow-hidden rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <legend class="px-1 font-display text-lg font-semibold">Media paths</legend>
        <div class="text-sm">
          <span class="text-ink-500">Container media roots</span>
          <p class="mt-1 font-mono text-xs text-ink-700 dark:text-ink-300">
            {{ (store.settings?.media_roots || []).join(', ') || '—' }}
          </p>
          <span class="mt-1 block text-xs text-ink-500">
            Auto-discovered from Docker volume mounts under <code class="font-mono">/data</code> and
            <code class="font-mono">/media</code>. Not editable here.
          </span>
        </div>
        <label class="block text-sm">
          <span class="text-ink-500">Path mappings (one per line: Bazarr path => local path)</span>
          <textarea
            v-model="form.path_mappings"
            rows="4"
            class="mt-1 w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 font-mono text-xs dark:border-ink-600"
            placeholder="/movies => /data/movies"
          />
          <span class="mt-1 block text-xs text-ink-500">
            Bazarr and Subtitle AI must see compatible media paths. Map prefixes when they differ.
          </span>
        </label>
      </fieldset>

      <button
        class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
        type="submit"
        :disabled="saving"
      >
        {{ saving ? 'Saving…' : 'Save providers' }}
      </button>
    </form>
  </section>
</template>
