<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import JobHistoryTable from '../components/JobHistoryTable.vue'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type {
  Candidate,
  JobAction,
  LanguageAvailability,
  LocalizationTask,
  MediaItem,
  MediaRef,
} from '../types'
import {
  canCancelTask,
  canRetryTask,
  isActiveTaskStatus,
  languageChipClass,
  taskStatusLabel,
} from '../utils/status'

const props = defineProps<{ id: string }>()
const store = useAppStore()

const media = ref<MediaItem | null>(null)
const localization = ref<LanguageAvailability[]>([])
const tasks = ref<LocalizationTask[]>([])
const actions = ref<JobAction[]>([])
const error = ref<string | null>(null)
const actionError = ref<string | null>(null)
const modalOpen = ref(false)
const selectedLang = ref<string | null>(null)
const busy = ref(false)
const retryingId = ref<number | null>(null)
let timer: number | undefined

const mediaId = computed(() => Number(props.id))

const visibleLanguages = computed(() =>
  localization.value.filter((lang) => lang.available || lang.task_status),
)

const selectedTask = computed(() => {
  if (!selectedLang.value) return fallbackTask.value
  return tasks.value.find((task) => task.target_language_code === selectedLang.value) || null
})

const fallbackTask = computed(() => {
  return (
    tasks.value.find((task) => isActiveTaskStatus(task.status)) ||
    tasks.value.find((task) => canRetryTask(task.status)) ||
    tasks.value[0] ||
    null
  )
})

const visibleActions = computed(() => {
  if (!selectedLang.value) return actions.value
  return actions.value.filter(
    (item) => !item.target_language || item.target_language === selectedLang.value,
  )
})

const matchedCandidate = computed<Candidate | null>(() => {
  if (!media.value) return null
  return (
    store.candidates.find((item) => {
      if (media.value!.bazarr_movie_id != null && item.bazarr_movie_id === media.value!.bazarr_movie_id) {
        return true
      }
      if (
        media.value!.bazarr_episode_id != null &&
        item.bazarr_episode_id === media.value!.bazarr_episode_id
      ) {
        return true
      }
      return Boolean(media.value!.path && item.media_path === media.value!.path)
    }) || null
  )
})

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

const mediaMeta = computed(() => {
  if (!media.value) return ''
  const parts: string[] = []
  if (media.value.year) parts.push(String(media.value.year))
  parts.push(media.value.media_type)
  if (media.value.media_type === 'episode' && media.value.season != null && media.value.episode != null) {
    parts.push(`S${String(media.value.season).padStart(2, '0')}E${String(media.value.episode).padStart(2, '0')}`)
  }
  if (media.value.episode_title) parts.push(media.value.episode_title)
  return parts.join(' · ')
})

const anyActive = computed(() => tasks.value.some((task) => isActiveTaskStatus(task.status)))

const showDiagnostics = computed(() => {
  const candidate = matchedCandidate.value
  if (!candidate) return false
  return Boolean(
    candidate.has_embedded ||
      candidate.source_subtitle_path ||
      candidate.target_subtitle_path ||
      candidate.reason,
  )
})

function languageLabel(lang: LanguageAvailability) {
  const status = lang.task_status
  if (status) return taskStatusLabel(status)
  if (lang.available) return 'Available'
  return '—'
}

function selectLanguage(code: string) {
  selectedLang.value = selectedLang.value === code ? null : code
}

function pickDefaultLanguage() {
  if (selectedLang.value && visibleLanguages.value.some((lang) => lang.language_code === selectedLang.value)) {
    return
  }
  const active = visibleLanguages.value.find((lang) => lang.task_status && isActiveTaskStatus(lang.task_status))
  const withTask = visibleLanguages.value.find((lang) => lang.task_id)
  selectedLang.value = active?.language_code || withTask?.language_code || null
}

async function load() {
  if (!Number.isFinite(mediaId.value)) {
    error.value = 'Invalid media id'
    return
  }
  try {
    const [mediaRow, loc, taskList, history] = await Promise.all([
      api.getMedia(mediaId.value),
      api.getMediaLocalization(mediaId.value),
      api.getLocalizationTasks({ media_item_id: mediaId.value, limit: 50 }),
      api.getMediaActions(mediaId.value),
    ])
    media.value = mediaRow
    localization.value = loc.languages
    tasks.value = taskList
    actions.value = history
    pickDefaultLanguage()
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function retryTask() {
  if (!selectedTask.value || busy.value) return
  busy.value = true
  actionError.value = null
  try {
    await api.retryLocalizationTask(selectedTask.value.id)
    await load()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function cancelTask() {
  if (!selectedTask.value || busy.value) return
  busy.value = true
  actionError.value = null
  try {
    await api.cancelLocalizationTask(selectedTask.value.id)
    await load()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function retryJob(jobId: number) {
  if (retryingId.value != null) return
  retryingId.value = jobId
  actionError.value = null
  try {
    await api.retryJob(jobId)
    await load()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    retryingId.value = null
  }
}

watch(
  () => props.id,
  () => {
    selectedLang.value = null
    load().catch(() => undefined)
  },
)

onMounted(() => {
  store.loadCandidatesCached().catch(() => undefined)
  load().catch(() => undefined)
  timer = window.setInterval(() => {
    if (anyActive.value) load().catch(() => undefined)
  }, 3000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-center gap-2 text-sm">
      <RouterLink class="text-accent hover:underline" to="/media">← Media</RouterLink>
    </div>

    <p v-if="error" class="text-sm text-red-600">{{ error }}</p>
    <div v-else-if="!media" class="text-sm text-ink-500">Loading…</div>

    <template v-else>
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h1 class="font-display text-2xl font-bold tracking-tight sm:text-3xl">
            {{ media.title }}
          </h1>
          <p class="mt-1 text-sm capitalize text-ink-500">{{ mediaMeta }}</p>
          <p v-if="media.path" class="mt-1 truncate text-xs text-ink-500" :title="media.path">
            {{ media.path }}
          </p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            v-if="selectedTask && canRetryTask(selectedTask.status)"
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            :disabled="busy"
            @click="retryTask"
          >
            Retry
          </button>
          <button
            v-if="selectedTask && canCancelTask(selectedTask.status)"
            type="button"
            class="rounded-md border border-red-300 px-3 py-1.5 text-sm font-semibold text-red-700 dark:border-red-800 dark:text-red-300"
            :disabled="busy"
            @click="cancelTask"
          >
            Cancel
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
            @click="modalOpen = true"
          >
            Request subtitles
          </button>
        </div>
      </div>

      <p
        v-if="actionError"
        class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
      >
        {{ actionError }}
      </p>

      <div class="flex flex-wrap gap-2">
        <button
          v-for="lang in visibleLanguages"
          :key="lang.language_code"
          type="button"
          class="rounded-full border px-3 py-1 text-xs font-semibold"
          :class="[
            languageChipClass(lang.task_status, lang.available),
            selectedLang === lang.language_code ? 'ring-2 ring-accent ring-offset-1 dark:ring-offset-ink-900' : '',
          ]"
          @click="selectLanguage(lang.language_code)"
        >
          {{ lang.language_name || lang.language_code }}
          <span class="ml-1 font-normal opacity-80">{{ languageLabel(lang) }}</span>
        </button>
        <p v-if="!visibleLanguages.length" class="text-sm text-ink-500">
          No localized subtitles requested for this media.
        </p>
      </div>

      <details
        v-if="showDiagnostics"
        class="rounded-xl border border-ink-200 bg-white/60 dark:border-ink-800 dark:bg-ink-900/40"
      >
        <summary class="cursor-pointer px-4 py-3 text-sm font-medium text-ink-600 dark:text-ink-300">
          Source and tracks
        </summary>
        <div class="space-y-3 border-t border-ink-200 px-4 py-3 text-sm dark:border-ink-800">
          <p v-if="matchedCandidate?.reason" class="text-ink-600 dark:text-ink-300">
            {{ matchedCandidate.reason }}
          </p>
          <p v-if="matchedCandidate?.source_subtitle_path" class="break-all text-xs text-ink-500">
            Source: {{ matchedCandidate.source_subtitle_path }}
          </p>
          <p v-if="matchedCandidate?.target_subtitle_path" class="break-all text-xs text-ink-500">
            Target: {{ matchedCandidate.target_subtitle_path }}
          </p>
          <div v-if="matchedCandidate?.has_embedded" class="flex flex-wrap gap-1.5">
            <span
              v-for="(track, idx) in matchedCandidate.embedded_subtitles"
              :key="`${track.label}-${idx}`"
              class="inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
              :class="
                track.kind === 'text'
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200'
                  : track.kind === 'image'
                    ? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200'
                    : 'border-ink-300 bg-ink-50 text-ink-600 dark:border-ink-700 dark:bg-ink-950/50 dark:text-ink-300'
              "
            >
              Embedded {{ track.label }}
            </span>
          </div>
        </div>
      </details>

      <div class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="font-display text-lg font-bold">History</h2>
            <p class="mt-1 text-sm text-ink-500">
              Request, extract, and translate runs for this
              {{ media.media_type === 'episode' ? 'episode' : 'title' }}.
            </p>
          </div>
          <p class="text-sm text-ink-500">{{ visibleActions.length }} total</p>
        </div>
        <div class="mt-4">
          <JobHistoryTable
            :actions="visibleActions"
            empty-message="No runs yet."
            :retrying-id="retryingId"
            @retry="retryJob"
          />
        </div>
      </div>
    </template>

    <RequestSubtitlesModal
      :open="modalOpen"
      :initial-media="modalMedia"
      :initial-language="selectedLang"
      @close="modalOpen = false"
      @created="load"
    />
  </section>
</template>
