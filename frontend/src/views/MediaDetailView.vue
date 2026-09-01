<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import JobHistoryTable from '../components/JobHistoryTable.vue'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import RequestDubModal from '../components/RequestDubModal.vue'
import RunningJobsPanel from '../components/RunningJobsPanel.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import { onLiveEvent } from '../stores/events'
import type {
  Candidate,
  Job,
  JobAction,
  LanguageAvailability,
  LocalizationTask,
  MediaItem,
  MediaRef,
} from '../types'
import {
  canCancelTask,
  canRetryBazarrSync,
  canRetryTask,
  isActiveTaskStatus,
  isSupersededLanguageBadge,
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
const detailLoadWarning = ref<string | null>(null)
const actionError = ref<string | null>(null)
const modalOpen = ref(false)
const dubModalOpen = ref(false)
const busy = ref(false)
const bazarrSyncBusy = ref(false)
const retryingId = ref<number | null>(null)
let timer: number | undefined
let stopLive: (() => void) | undefined

const mediaId = computed(() => Number(props.id))

const visibleLanguages = computed(() =>
  localization.value.filter(
    (lang) =>
      (lang.available || lang.task_status) && !isSupersededLanguageBadge(lang, localization.value),
  ),
)

const dubbedTasks = computed(() => {
  const latestByLanguage = new Map<string, LocalizationTask>()
  for (const task of tasks.value) {
    if ((task.capability || 'subtitles') !== 'audio') continue
    const existing = latestByLanguage.get(task.target_language_code)
    if (!existing || task.id > existing.id) latestByLanguage.set(task.target_language_code, task)
  }
  return [...latestByLanguage.values()].filter((task) => task.status === 'completed')
})

const selectedTask = computed(() => {
  return (
    tasks.value.find((task) => isActiveTaskStatus(task.status)) ||
    tasks.value.find((task) => canRetryTask(task.status)) ||
    tasks.value[0] ||
    null
  )
})

const historyActions = computed(() => {
  const runningJob = new Set(['pending', 'processing'])
  return [...actions.value]
    .filter((item) => {
      if (item.kind === 'task' && isActiveTaskStatus(item.status)) return false
      if (item.kind !== 'task' && runningJob.has(item.status)) return false
      return true
    })
    .sort((a, b) => {
      const aTime = a.datetime || ''
      const bTime = b.datetime || ''
      if (aTime !== bTime) return bTime.localeCompare(aTime)
      return b.id - a.id
    })
})

const verifyFailedTasks = computed(() => tasks.value.filter((task) => canRetryBazarrSync(task)))

const runningTasks = computed(() =>
  tasks.value.filter((task) => isActiveTaskStatus(task.status) && !canRetryBazarrSync(task)),
)

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
  if (media.value.media_type !== 'episode' && media.value.episode_title) {
    parts.push(media.value.episode_title)
  }
  return parts.join(' · ')
})

const mediaHeading = computed(() => {
  if (!media.value || media.value.media_type !== 'episode') return media.value?.title || ''
  if (media.value.season == null || media.value.episode == null || !media.value.episode_title) {
    return media.value.title
  }
  const code = `S${String(media.value.season).padStart(2, '0')}E${String(media.value.episode).padStart(2, '0')}`
  return `${code} - ${media.value.episode_title}`
})

const anyActive = computed(() =>
  tasks.value.some((task) => isActiveTaskStatus(task.status) && !canRetryBazarrSync(task)),
)

const detailJob = computed<Job | null>(() => {
  const jobs: Job[] = []
  for (const task of tasks.value) {
    jobs.push(...(task.executions || []))
  }
  if (!jobs.length) return null
  const newest = (items: Job[]) => [...items].sort((a, b) => b.id - a.id)[0]
  const translate = jobs.filter((job) => (job.job_kind || 'translate') === 'translate')
  return newest(translate.length ? translate : jobs)
})

const sourceSubtitlePath = computed(
  () =>
    detailJob.value?.source_subtitle_path ||
    matchedCandidate.value?.source_subtitle_path ||
    null,
)

const targetSubtitlePath = computed(
  () =>
    detailJob.value?.target_subtitle_path ||
    matchedCandidate.value?.target_subtitle_path ||
    null,
)

const dubbedPath = computed(() => {
  const dubJobs = tasks.value
    .flatMap((task) => task.executions || [])
    .filter((job) => job.job_kind === 'dub' && job.status === 'completed')
  if (!dubJobs.length) return null
  return [...dubJobs].sort((a, b) => b.id - a.id)[0].target_subtitle_path
})

const showEmbeddedTracks = computed(() => Boolean(matchedCandidate.value?.has_embedded))

function languageTask(lang: LanguageAvailability) {
  return tasks.value.find((task) => task.id === lang.task_id) || null
}

function languageVerificationFailed(lang: LanguageAvailability) {
  const task = languageTask(lang)
  return Boolean(task && canRetryBazarrSync(task))
}

function languageLabel(lang: LanguageAvailability) {
  if (languageVerificationFailed(lang)) return 'Verification failed'
  const status = lang.task_status
  if (status) return taskStatusLabel(status, lang.task_substate)
  if (lang.available) return 'Available'
  return '—'
}

async function resolveWithin<T>(promise: Promise<T>, label: string, timeoutMs = 15_000): Promise<T> {
  let timeout: number | undefined
  try {
    return await Promise.race([
      promise,
      new Promise<T>((_resolve, reject) => {
        timeout = window.setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs)
      }),
    ])
  } finally {
    if (timeout != null) window.clearTimeout(timeout)
  }
}

async function load() {
  if (!Number.isFinite(mediaId.value)) {
    error.value = 'Invalid media id'
    return
  }
  error.value = null
  detailLoadWarning.value = null
  media.value = null
  try {
    // The media row is local and must not be hidden behind slower optional
    // calls (notably the live Bazarr availability check).
    media.value = await resolveWithin(api.getMedia(mediaId.value), 'Media details')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    return
  }

  const results = await Promise.allSettled([
    resolveWithin(api.getMediaLocalization(mediaId.value), 'Subtitle availability'),
    resolveWithin(
      api.getLocalizationTasks({ media_item_id: mediaId.value, limit: 50, include_detail: true }),
      'Localization task history',
    ),
    resolveWithin(api.getMediaActions(mediaId.value), 'Job history'),
  ])
  const [localizationResult, tasksResult, actionsResult] = results
  const unavailable: string[] = []

  if (localizationResult.status === 'fulfilled') {
    localization.value = localizationResult.value.languages
  } else {
    localization.value = []
    unavailable.push('subtitle availability')
  }
  if (tasksResult.status === 'fulfilled') {
    tasks.value = tasksResult.value
  } else {
    tasks.value = []
    unavailable.push('localization tasks')
  }
  if (actionsResult.status === 'fulfilled') {
    actions.value = actionsResult.value
  } else {
    actions.value = []
    unavailable.push('job history')
  }
  if (unavailable.length) {
    detailLoadWarning.value = `Could not load ${unavailable.join(', ')}. You can refresh to try again.`
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

async function retryBazarrSync() {
  const targets = verifyFailedTasks.value
  if (!targets.length || busy.value) return
  busy.value = true
  bazarrSyncBusy.value = true
  actionError.value = null
  try {
    for (const task of targets) {
      await api.retryLocalizationTask(task.id)
    }
    await load()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    bazarrSyncBusy.value = false
    busy.value = false
  }
}

async function cancelTask(taskId?: number) {
  const id = taskId ?? selectedTask.value?.id
  if (!id || busy.value) return
  busy.value = true
  actionError.value = null
  try {
    await api.cancelLocalizationTask(id)
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
    load().catch(() => undefined)
  },
)

onMounted(() => {
  store.loadCandidatesCached().catch(() => undefined)
  store.loadSettings().catch(() => undefined)
  load().catch(() => undefined)
  timer = window.setInterval(() => {
    if (anyActive.value) load().catch(() => undefined)
  }, 30000)
  stopLive = onLiveEvent((event) => {
    if (event.type === 'hello') return
    if (event.media_item_id === mediaId.value || event.task_id) {
      load().catch(() => undefined)
    }
  })
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  stopLive?.()
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
      <p
        v-if="detailLoadWarning"
        class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
      >
        {{ detailLoadWarning }}
      </p>
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <p
            v-if="media.media_type === 'episode' && media.series_title"
            class="font-display text-lg font-semibold text-ink-600 dark:text-ink-300"
          >
            {{ media.series_title }}
          </p>
          <h1 class="font-display text-2xl font-bold tracking-tight sm:text-3xl">
            {{ mediaHeading }}
          </h1>
          <p class="mt-1 text-sm capitalize text-ink-500">{{ mediaMeta }}</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            v-if="selectedTask && canRetryTask(selectedTask.status)"
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            title="Retry"
            aria-label="Retry"
            :disabled="busy"
            @click="retryTask"
          >
            Retry
          </button>
          <button
            v-if="
              selectedTask &&
              canCancelTask(selectedTask.status) &&
              !runningTasks.length &&
              !canRetryBazarrSync(selectedTask)
            "
            type="button"
            class="rounded-md border border-red-300 px-3 py-1.5 text-sm font-semibold text-red-700 dark:border-red-800 dark:text-red-300"
            title="Cancel"
            aria-label="Cancel"
            :disabled="busy"
            @click="cancelTask()"
          >
            Cancel
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
            title="Request subtitles"
            aria-label="Request subtitles"
            @click="modalOpen = true"
          >
            Request subtitles
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
            title="Create or replace a Portuguese TTS dub"
            aria-label="Request dub"
            :disabled="busy || anyActive"
            @click="dubModalOpen = true"
          >
            Request dub
          </button>
          <RouterLink
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
            :to="{
              name: 'dub-cast',
              params: { id: mediaId },
              query: { language: selectedTask?.target_language_code || 'pt-PT' },
            }"
          >
            Voice cast
          </RouterLink>
          <details v-if="verifyFailedTasks.length" class="relative">
            <summary
              class="cursor-pointer list-none rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600 [&::-webkit-details-marker]:hidden"
            >
              More
            </summary>
            <div
              class="absolute right-0 z-10 mt-1 min-w-[12rem] rounded-md border border-ink-200 bg-white p-1 shadow-lg dark:border-ink-700 dark:bg-ink-900"
            >
              <button
                type="button"
                class="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-semibold hover:bg-ink-100 disabled:cursor-wait disabled:opacity-70 dark:hover:bg-ink-800"
                title="Retry Bazarr sync"
                :disabled="busy"
                :aria-busy="bazarrSyncBusy"
                @click="retryBazarrSync"
              >
                <svg
                  v-if="bazarrSyncBusy"
                  class="h-4 w-4 animate-spin"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                >
                  <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                  <path d="M21 3v6h-6" />
                </svg>
                {{ bazarrSyncBusy ? 'Syncing with Bazarr…' : 'Retry Bazarr sync' }}
              </button>
            </div>
          </details>
        </div>
      </div>

      <p
        v-if="actionError"
        class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
      >
        {{ actionError }}
      </p>

      <div class="flex flex-wrap gap-2">
        <span
          v-for="lang in visibleLanguages"
          :key="lang.language_code"
          class="rounded-full border px-3 py-1 text-xs font-semibold"
          :class="
            languageChipClass(lang.task_status, lang.available, {
              verificationFailed: languageVerificationFailed(lang),
            })
          "
        >
          {{ lang.language_name || lang.language_code }}
          <span class="ml-1 font-normal opacity-80">{{ languageLabel(lang) }}</span>
        </span>
        <span
          v-for="task in dubbedTasks"
          :key="`dub-${task.id}`"
          class="rounded-full border px-3 py-1 text-xs font-semibold"
          :class="languageChipClass('completed', true)"
        >
          {{ task.target_language_name }}
          <span class="ml-1 font-normal opacity-80">Dubbed</span>
        </span>
        <p v-if="!visibleLanguages.length && !dubbedTasks.length" class="text-sm text-ink-500">
          No localized subtitles requested for this media.
        </p>
      </div>

      <details class="rounded-md border border-ink-200 bg-white dark:border-ink-800 dark:bg-ink-900">
        <summary class="cursor-pointer px-4 py-3 text-sm font-semibold text-ink-700 dark:text-ink-200">
          Details
        </summary>
        <dl class="grid gap-4 border-t border-ink-200 px-4 py-4 text-sm dark:border-ink-800 sm:grid-cols-2">
          <div>
            <dt class="text-ink-500">Media</dt>
            <dd class="mt-1 break-all">{{ media.path || '—' }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Source subtitle</dt>
            <dd class="mt-1 break-all">{{ sourceSubtitlePath || '—' }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Target subtitle</dt>
            <dd class="mt-1 break-all">{{ targetSubtitlePath || '—' }}</dd>
            <template v-if="dubbedPath">
              <dt class="mt-4 text-ink-500">Dubbed output</dt>
              <dd class="mt-1 break-all">{{ dubbedPath }}</dd>
            </template>
          </div>
          <div v-if="showEmbeddedTracks" class="sm:col-span-2">
            <dt class="text-ink-500">Embedded tracks</dt>
            <dd class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="(track, idx) in matchedCandidate?.embedded_subtitles"
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
            </dd>
          </div>
        </dl>
      </details>

      <RunningJobsPanel
        v-if="runningTasks.length"
        :tasks="runningTasks"
        :busy="busy"
        @cancel="cancelTask"
      />

      <div class="rounded-xl border border-ink-200 bg-white/80 p-5 dark:border-ink-800 dark:bg-ink-900/60">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="font-display text-lg font-bold">History</h2>
          </div>
          <p class="text-sm text-ink-500">{{ historyActions.length }} total</p>
        </div>
        <div class="mt-4">
          <JobHistoryTable
            :actions="historyActions"
            empty-message="No runs yet."
            :page-size="10"
            :retrying-id="retryingId"
            @retry="retryJob"
          />
        </div>
      </div>
    </template>

    <RequestSubtitlesModal
      :open="modalOpen"
      :initial-media="modalMedia"
      @close="modalOpen = false"
      @created="load"
    />
    <RequestDubModal
      :open="dubModalOpen"
      :initial-media="modalMedia"
      :initial-language="selectedTask?.target_language_code || store.settings?.target_language.code"
      @close="dubModalOpen = false"
      @created="load"
    />
  </section>
</template>
