<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../services/api'
import type { LanguageCatalogItem, MediaRef } from '../types'
import { mediaHrefForTaskId } from '../utils/mediaNav'

const props = defineProps<{
  open: boolean
  /** Pre-selected media (e.g. from candidates) — skips search. */
  initialMedia?: MediaRef | null
  initialLanguage?: string | null
}>()

const emit = defineEmits<{
  close: []
  created: [taskId: number]
}>()

const router = useRouter()
const query = ref('')
const searching = ref(false)
const searchError = ref<string | null>(null)
const results = ref<MediaRef[]>([])
const selected = ref<MediaRef | null>(null)
const languages = ref<LanguageCatalogItem[]>([])
const languageChoice = ref('')
const customLanguage = ref('')
const languageFilter = ref('')
const submitting = ref(false)
const submitError = ref<string | null>(null)
const existingTaskId = ref<number | null>(null)

const filteredLanguages = computed(() => {
  const q = languageFilter.value.trim().toLowerCase()
  if (!q) return languages.value
  return languages.value.filter(
    (l) =>
      l.display_name.toLowerCase().includes(q) ||
      l.code.toLowerCase().includes(q) ||
      l.aliases.some((a) => a.toLowerCase().includes(q)),
  )
})

const targetLanguage = computed(() => {
  const custom = customLanguage.value.trim()
  if (custom) return custom
  return languageChoice.value
})

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    searchError.value = null
    submitError.value = null
    existingTaskId.value = null
    results.value = []
    query.value = ''
    customLanguage.value = ''
    languageFilter.value = ''
    selected.value = props.initialMedia ?? null
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

let searchTimer: number | undefined
watch(query, (q) => {
  if (selected.value && props.initialMedia) return
  if (searchTimer) window.clearTimeout(searchTimer)
  if (q.trim().length < 2) {
    results.value = []
    return
  }
  searchTimer = window.setTimeout(() => {
    void runSearch()
  }, 300)
})

async function runSearch() {
  searching.value = true
  searchError.value = null
  try {
    results.value = await api.searchMedia(query.value.trim())
  } catch (err) {
    searchError.value = err instanceof Error ? err.message : String(err)
    results.value = []
  } finally {
    searching.value = false
  }
}

function selectMedia(item: MediaRef) {
  selected.value = item
  results.value = []
  query.value = item.title
}

function clearSelection() {
  selected.value = null
  query.value = ''
  results.value = []
}

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
  if (!selected.value || !targetLanguage.value || submitting.value) return
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
    try {
      const task = await api.createLocalizationTask(media.id, {
        target_language: targetLanguage.value,
        capability: 'subtitles',
      })
      emit('created', task.id)
      emit('close')
      await router.push(`/media/${task.media_item_id}`)
    } catch (err) {
      const e = err as Error & { code?: string; taskId?: number }
      if (e.code === 'active_task_exists' && e.taskId) {
        existingTaskId.value = e.taskId
        submitError.value =
          'A localization task for this media and language is already running.'
        return
      }
      throw err
    }
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}

async function openExisting() {
  if (!existingTaskId.value) return
  try {
    const href = await mediaHrefForTaskId(existingTaskId.value)
    emit('close')
    await router.push(href)
  } catch {
    await router.push('/media')
  }
}

onMounted(() => {
  /* languages loaded on open */
})
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/50 p-4 sm:items-center"
    role="dialog"
    aria-modal="true"
    aria-labelledby="request-subtitles-title"
    @click.self="emit('close')"
  >
    <div
      class="w-full max-w-lg rounded-xl border border-ink-200 bg-white p-5 shadow-xl dark:border-ink-700 dark:bg-ink-900"
    >
      <div class="flex items-start justify-between gap-3">
        <div>
          <h2 id="request-subtitles-title" class="font-display text-xl font-bold">
            Request subtitles
          </h2>
          <p class="mt-1 text-sm text-ink-500 dark:text-ink-300">
            Pick any Bazarr movie or episode and a target language.
          </p>
        </div>
        <button
          type="button"
          class="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
          @click="emit('close')"
        >
          Close
        </button>
      </div>

      <div class="mt-5 space-y-4">
        <div>
          <label class="block text-sm font-medium text-ink-700 dark:text-ink-200">Media</label>
          <div v-if="selected" class="mt-1.5 flex items-center justify-between gap-2 rounded-md border border-ink-200 bg-ink-50 px-3 py-2 dark:border-ink-700 dark:bg-ink-800">
            <div class="min-w-0">
              <p class="truncate font-medium">{{ mediaLabel(selected) }}</p>
              <p class="text-xs uppercase tracking-wide text-ink-500">{{ selected.media_type }}</p>
            </div>
            <button
              type="button"
              class="shrink-0 text-sm text-accent hover:underline"
              @click="clearSelection"
            >
              Change
            </button>
          </div>
          <template v-else>
            <input
              v-model="query"
              type="search"
              class="mt-1.5 w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-sm dark:border-ink-600 dark:bg-ink-950"
              placeholder="Search media…"
              autocomplete="off"
            />
            <p v-if="searching" class="mt-1 text-xs text-ink-500">Searching…</p>
            <p v-else-if="searchError" class="mt-1 text-xs text-red-600">{{ searchError }}</p>
            <p
              v-else-if="query.trim().length >= 2 && !results.length"
              class="mt-1 text-xs text-ink-500"
            >
              No media found in Bazarr.
            </p>
            <ul
              v-if="results.length"
              class="mt-2 max-h-48 overflow-auto rounded-md border border-ink-200 dark:border-ink-700"
            >
              <li v-for="item in results" :key="item.external_id">
                <button
                  type="button"
                  class="flex w-full flex-col items-start gap-0.5 border-b border-ink-100 px-3 py-2 text-left text-sm last:border-0 hover:bg-ink-50 dark:border-ink-800 dark:hover:bg-ink-800"
                  @click="selectMedia(item)"
                >
                  <span class="font-medium">{{ mediaLabel(item) }}</span>
                  <span class="text-xs uppercase text-ink-500">{{ item.media_type }}</span>
                </button>
              </li>
            </ul>
          </template>
        </div>

        <div>
          <label class="block text-sm font-medium text-ink-700 dark:text-ink-200">
            Target language
          </label>
          <input
            v-model="languageFilter"
            type="search"
            class="mt-1.5 w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-sm dark:border-ink-600 dark:bg-ink-950"
            placeholder="Search languages…"
            :disabled="!!customLanguage.trim()"
          />
          <select
            v-model="languageChoice"
            class="mt-2 w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-sm dark:border-ink-600 dark:bg-ink-950"
            :disabled="!!customLanguage.trim()"
          >
            <option v-for="lang in filteredLanguages" :key="lang.code" :value="lang.code">
              {{ lang.display_name }}
            </option>
          </select>
          <p class="mt-2 text-xs font-medium text-ink-500">or type:</p>
          <input
            v-model="customLanguage"
            type="text"
            class="mt-1.5 w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-sm dark:border-ink-600 dark:bg-ink-950"
            placeholder="ja-JP"
          />
          <p v-if="!languages.length" class="mt-1 text-xs text-ink-500">
            No recognized languages. You can type a language name or code.
          </p>
        </div>

        <p v-if="submitError" class="text-sm text-red-600">{{ submitError }}</p>
        <button
          v-if="existingTaskId"
          type="button"
          class="text-sm font-semibold text-accent hover:underline"
          @click="openExisting"
        >
          View existing task
        </button>

        <div class="flex justify-end gap-2 pt-1">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-medium dark:border-ink-600"
            @click="emit('close')"
          >
            Cancel
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            :disabled="!selected || !targetLanguage || submitting"
            @click="submit"
          >
            {{ submitting ? 'Requesting…' : 'Request subtitles' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
