<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import JobHistoryTable from '../components/JobHistoryTable.vue'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import RunningJobsPanel from '../components/RunningJobsPanel.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
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
const busy = ref(false)
const retryingId = ref<number | null>(null)
const canTranscribe = ref(false)
const transcribeReason = ref<string | null>(null)
let timer: number | undefined

const mediaId = computed(() => Number(props.id))

const visibleLanguages = computed(() =>
  localization.value.filter((lang) => lang.available || lang.task_status),
)

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
  if (media.value.media_type === 'episode' && media.value.season != null && media.value.episode != null) {
    parts.push(`S${String(media.value.season).padStart(2, '0')}E${String(media.value.episode).padStart(2, '0')}`)
  }
  if (media.value.episode_title) parts.push(media.value.episode_title)
  return parts.join(' · ')
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

const detailKind = computed(() => {
  if (detailJob.value) {
    const kind = detailJob.value.job_kind || 'translate'
    const trigger = detailJob.value.trigger_type === 'automatic' ? 'automatic' : 'manual'
    return `${kind} (${trigger})`
  }
  if (selectedTask.value) {
    return `${selectedTask.value.capability} (${selectedTask.value.origin})`
  }
  return null
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

const detailModel = computed(
  () => detailJob.value?.model || selectedTask.value?.ai?.model_id || null,
)

const detailReason = computed(
  () => matchedCandidate.value?.reason || detailJob.value?.reason_code || null,
)

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

async function load() {
  if (!Number.isFinite(mediaId.value)) {
    error.value = 'Invalid media id'
    return
  }
  try {
    const [mediaRow, loc, taskList, history] = await Promise.all([
      api.getMedia(mediaId.value),
      api.getMediaLocalization(mediaId.value),
      api.getLocalizationTasks({ media_item_id: mediaId.value, limit: 50, include_detail: true }),
      api.getMediaActions(mediaId.value),
    ])
    media.value = mediaRow
    localization.value = loc.languages
    canTranscribe.value = Boolean(loc.can_transcribe)
    transcribeReason.value = loc.transcribe_reason || null
    tasks.value = taskList
    actions.value = history
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

async function retryBazarrSync() {
  const targets = verifyFailedTasks.value
  if (!targets.length || busy.value) return
  busy.value = true
  actionError.value = null
  try {
    for (const task of targets) {
      await api.retryLocalizationTask(task.id)
    }
    await load()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
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

async function transcribeAudio() {
  if (!media.value || busy.value) return
  const ok = window.confirm(
    'Transcribe audio from this file? On CPU this can take as long as the video itself, and the first run downloads a Whisper model.',
  )
  if (!ok) return
  busy.value = true
  actionError.value = null
  try {
    await api.transcribeMedia(mediaId.value, {
      target_language:
        selectedTask.value?.target_language_code || store.settings?.target_language.code,
    })
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
            v-if="verifyFailedTasks.length"
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            title="Retry Bazarr sync"
            aria-label="Retry Bazarr sync"
            :disabled="busy"
            @click="retryBazarrSync"
          >
            Retry Bazarr sync
          </button>
          <button
            v-if="canTranscribe"
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            :title="transcribeReason || 'Transcribe audio when no subtitle source is available'"
            aria-label="Transcribe audio"
            :disabled="busy || anyActive"
            @click="transcribeAudio"
          >
            Transcribe audio
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
        <p v-if="!visibleLanguages.length" class="text-sm text-ink-500">
          No localized subtitles requested for this media.
        </p>
      </div>

      <dl class="grid gap-4 rounded-xl border border-ink-200 bg-white/80 p-5 text-sm dark:border-ink-800 dark:bg-ink-900/60 sm:grid-cols-2">
        <div>
          <dt class="text-ink-500">Media</dt>
          <dd class="mt-1 break-all">{{ media.path || '—' }}</dd>
        </div>
        <div>
          <dt class="text-ink-500">Kind</dt>
          <dd class="mt-1 capitalize">{{ detailKind || '—' }}</dd>
        </div>
        <div>
          <dt class="text-ink-500">Type</dt>
          <dd class="mt-1 capitalize">{{ media.media_type }}</dd>
        </div>
        <div>
          <dt class="text-ink-500">Source subtitle</dt>
          <dd class="mt-1 break-all">{{ sourceSubtitlePath || '—' }}</dd>
        </div>
        <div>
          <dt class="text-ink-500">Target subtitle</dt>
          <dd class="mt-1 break-all">{{ targetSubtitlePath || '—' }}</dd>
        </div>
        <div>
          <dt class="text-ink-500">Model</dt>
          <dd class="mt-1">{{ detailModel || '—' }}</dd>
        </div>
        <div v-if="detailReason" class="sm:col-span-2">
          <dt class="text-ink-500">Reason</dt>
          <dd class="mt-1">{{ detailReason }}</dd>
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
            :page-size="5"
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
  </section>
</template>
