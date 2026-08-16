<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import { api } from '../services/api'
import type { LanguageAvailability, LocalizationTask, MediaItem, MediaRef } from '../types'

const props = defineProps<{ id: string }>()

const media = ref<MediaItem | null>(null)
const localization = ref<LanguageAvailability[]>([])
const tasks = ref<LocalizationTask[]>([])
const error = ref<string | null>(null)
const modalOpen = ref(false)

const mediaId = computed(() => Number(props.id))

const modalMedia = computed<MediaRef | null>(() => {
  if (!media.value) return null
  return {
    provider_id: media.value.provider_id,
    external_id: media.value.external_id,
    media_type: media.value.media_type as MediaRef['media_type'],
    title: media.value.title,
    year: media.value.year,
    season: media.value.season,
    episode: media.value.episode,
    episode_title: media.value.episode_title,
    path: media.value.path,
    parent_external_id: null,
    bazarr_movie_id: media.value.bazarr_movie_id,
    bazarr_series_id: media.value.bazarr_series_id,
    bazarr_episode_id: media.value.bazarr_episode_id,
  }
})

function statusMark(lang: LanguageAvailability) {
  if (lang.task_status === 'processing' || lang.task_status === 'verifying' || lang.task_status === 'planning') {
    return '⟳'
  }
  if (lang.task_status === 'waiting_for_source' || lang.task_status === 'requested') {
    return '…'
  }
  if (lang.available || lang.task_status === 'completed') return '✓'
  if (lang.task_status === 'failed') return '✗'
  return '—'
}

function statusText(lang: LanguageAvailability) {
  if (lang.task_status === 'processing' || lang.task_status === 'verifying' || lang.task_status === 'planning') {
    return 'Translating'
  }
  if (lang.task_status === 'waiting_for_source' || lang.task_status === 'requested') {
    return 'Waiting for source'
  }
  if (lang.available || lang.task_status === 'completed') return 'Available'
  if (lang.task_status === 'failed') return 'Failed'
  return '—'
}

async function load() {
  if (!Number.isFinite(mediaId.value)) {
    error.value = 'Invalid media id'
    return
  }
  try {
    media.value = await api.getMedia(mediaId.value)
    const loc = await api.getMediaLocalization(mediaId.value)
    localization.value = loc.languages
    tasks.value = await api.getLocalizationTasks({
      media_item_id: mediaId.value,
      limit: 50,
    })
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

watch(
  () => props.id,
  () => {
    load().catch(() => undefined)
  },
)

onMounted(() => {
  load().catch(() => undefined)
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <RouterLink class="text-accent hover:underline" to="/tasks">← Tasks</RouterLink>
    </div>

    <p v-if="error" class="text-sm text-red-600">{{ error }}</p>
    <div v-else-if="!media" class="text-sm text-ink-500">Loading…</div>

    <template v-else>
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="font-display text-2xl font-bold uppercase tracking-tight sm:text-3xl">
            {{ media.title }}
          </h1>
          <p class="mt-1 text-sm text-ink-500">
            <span v-if="media.year">{{ media.year }} · </span>
            <span class="capitalize">{{ media.media_type }}</span>
          </p>
        </div>
        <button
          type="button"
          class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
          @click="modalOpen = true"
        >
          Request subtitles
        </button>
      </div>

      <div class="rounded-xl border border-ink-200 p-4 dark:border-ink-700">
        <h2 class="text-sm font-semibold uppercase tracking-wide text-ink-500">Subtitles</h2>
        <ul v-if="localization.length" class="mt-3 divide-y divide-ink-100 dark:divide-ink-800">
          <li
            v-for="lang in localization"
            :key="lang.language_code"
            class="flex items-center justify-between gap-3 py-2 text-sm"
          >
            <div>
              <span class="font-medium">{{ lang.language_name || lang.language_code }}</span>
              <span class="ml-2 text-xs text-ink-500">{{ lang.language_code }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="font-semibold">{{ statusMark(lang) }}</span>
              <span class="text-ink-600 dark:text-ink-300">{{ statusText(lang) }}</span>
              <RouterLink
                v-if="lang.task_id"
                class="text-xs text-accent hover:underline"
                :to="`/tasks/${lang.task_id}`"
              >
                Task
              </RouterLink>
            </div>
          </li>
        </ul>
        <p v-else class="mt-3 text-sm text-ink-500">
          No localized subtitles requested for this media.
        </p>
        <button
          type="button"
          class="mt-4 rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
          @click="modalOpen = true"
        >
          Request subtitles
        </button>
      </div>

      <div class="rounded-xl border border-ink-200 p-4 dark:border-ink-700">
        <h2 class="text-sm font-semibold uppercase tracking-wide text-ink-500">Audio</h2>
        <p class="mt-3 text-sm text-ink-500">Coming later — audio localization is not available yet.</p>
      </div>

      <div class="rounded-xl border border-ink-200 p-4 dark:border-ink-700">
        <h2 class="text-sm font-semibold uppercase tracking-wide text-ink-500">Tasks</h2>
        <ul v-if="tasks.length" class="mt-3 divide-y divide-ink-100 dark:divide-ink-800">
          <li
            v-for="task in tasks"
            :key="task.id"
            class="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
          >
            <RouterLink class="font-medium text-accent hover:underline" :to="`/tasks/${task.id}`">
              {{ task.target_language_name }}
            </RouterLink>
            <span class="capitalize text-ink-500">{{ task.status.replaceAll('_', ' ') }}</span>
          </li>
        </ul>
        <p v-else class="mt-3 text-sm text-ink-500">No tasks for this media yet.</p>
      </div>
    </template>

    <RequestSubtitlesModal
      :open="modalOpen"
      :initial-media="modalMedia"
      @close="modalOpen = false"
      @created="load"
    />
  </section>
</template>
