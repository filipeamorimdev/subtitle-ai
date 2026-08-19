<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import LanguageSelect from './LanguageSelect.vue'
import { api } from '../services/api'
import type { LanguageCatalogItem, MediaRef } from '../types'

const props = defineProps<{
  open: boolean
  /** Pre-selected media (e.g. media detail page). */
  initialMedia?: MediaRef | null
  initialLanguage?: string | null
}>()

const emit = defineEmits<{
  close: []
  created: []
}>()

const languages = ref<LanguageCatalogItem[]>([])
const languageChoice = ref('')
const submitting = ref(false)
const submitError = ref<string | null>(null)

const selected = computed(() => props.initialMedia ?? null)

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    submitError.value = null
    if (!languages.value.length) {
      try {
        languages.value = await api.getLanguages()
      } catch {
        languages.value = []
      }
    }
    if (props.initialLanguage) {
      languageChoice.value = props.initialLanguage
    } else if (languages.value.some((l) => l.code === 'pt-PT')) {
      languageChoice.value = 'pt-PT'
    } else if (languages.value[0]) {
      languageChoice.value = languages.value[0].code
    }
  },
)

function mediaLabel(item: MediaRef) {
  if (item.media_type === 'episode') {
    const ep =
      item.season != null && item.episode != null
        ? `S${String(item.season).padStart(2, '0')}E${String(item.episode).padStart(2, '0')}`
        : ''
    return [item.title, ep].filter(Boolean).join(' · ')
  }
  return [item.title, item.year].filter(Boolean).join(' · ')
}

async function submit() {
  if (!selected.value || !languageChoice.value || submitting.value) return
  submitting.value = true
  submitError.value = null
  try {
    const media = await api.ensureMedia({
      provider_id: selected.value.provider_id,
      external_id: selected.value.external_id,
      media_type: selected.value.media_type,
      title: selected.value.title,
      year: selected.value.year,
      path: selected.value.path,
      season: selected.value.season,
      episode: selected.value.episode,
      episode_title: selected.value.episode_title,
      bazarr_movie_id: selected.value.bazarr_movie_id,
      bazarr_series_id: selected.value.bazarr_series_id,
      bazarr_episode_id: selected.value.bazarr_episode_id,
      parent_external_id: selected.value.parent_external_id,
    })
    await api.dubMedia(media.id, {
      target_language: languageChoice.value,
      replace_existing: true,
    })
    emit('created')
    emit('close')
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/50 p-4 sm:items-center"
    role="dialog"
    aria-modal="true"
    aria-labelledby="request-dub-title"
    @click.self="emit('close')"
  >
    <div
      class="w-full max-w-lg rounded-xl border border-ink-200 bg-white p-5 shadow-xl dark:border-ink-700 dark:bg-ink-900"
    >
      <div class="flex items-start justify-between gap-3">
        <div>
          <h2 id="request-dub-title" class="font-display text-xl font-bold">Request dub</h2>
          <p class="mt-1 text-sm text-ink-500">
            Creates a TTS dub preview (.dub.mkv) beside the original. An existing dub for the
            selected language is replaced.
          </p>
        </div>
        <button
          type="button"
          class="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
          title="Close"
          aria-label="Close"
          @click="emit('close')"
        >
          Close
        </button>
      </div>

      <div class="mt-5 space-y-4">
        <div v-if="selected">
          <span class="block text-sm font-medium text-ink-700 dark:text-ink-200">Media</span>
          <div
            class="mt-1.5 rounded-md border border-ink-200 bg-ink-50 px-3 py-2 dark:border-ink-700 dark:bg-ink-800"
          >
            <p class="truncate font-medium">{{ mediaLabel(selected) }}</p>
            <p class="text-xs uppercase tracking-wide text-ink-500">{{ selected.media_type }}</p>
          </div>
        </div>

        <div>
          <span class="block text-sm font-medium text-ink-700 dark:text-ink-200">
            Target language
          </span>
          <LanguageSelect
            v-model="languageChoice"
            :languages="languages"
            placeholder="Select target language"
          />
          <p v-if="!languages.length" class="mt-1 text-xs text-ink-500">
            No recognized languages loaded.
          </p>
        </div>

        <p v-if="submitError" class="text-sm text-red-600">{{ submitError }}</p>

        <div class="flex justify-end gap-2 pt-1">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-medium dark:border-ink-600"
            title="Cancel"
            aria-label="Cancel"
            @click="emit('close')"
          >
            Cancel
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            title="Request dub"
            aria-label="Request dub"
            :disabled="!selected || !languageChoice || submitting"
            @click="submit"
          >
            {{ submitting ? 'Requesting…' : 'Request dub' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
