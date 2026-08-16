<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import { useAppStore } from '../stores/app'
import type { BatchJobsResult, Candidate, MediaRef } from '../types'

type CandidateFilter = 'ready' | 'extract' | 'need-source' | 'target-exists'

const FILTER_VALUES: CandidateFilter[] = ['ready', 'extract', 'need-source', 'target-exists']

const store = useAppStore()
const router = useRouter()
const route = useRoute()
const translatingKey = ref<string | null>(null)
const extractingKey = ref<string | null>(null)
const requestingKey = ref<string | null>(null)
const batchBusy = ref<'request' | 'extract' | 'translate' | null>(null)
const actionError = ref<string | null>(null)
const actionInfo = ref<string | null>(null)
const selectedKeys = ref<Set<string>>(new Set())
const categoryFilter = ref<CandidateFilter | null>(null)
const requestModalOpen = ref(false)
const requestModalMedia = ref<MediaRef | null>(null)
const requestModalLanguage = ref<string | null>(null)

function candidateToMediaRef(item: Candidate): MediaRef {
  const isMovie = item.media_type === 'movie'
  return {
    provider_id: 'bazarr',
    external_id: isMovie
      ? `movie:${item.bazarr_movie_id}`
      : `episode:${item.bazarr_episode_id}`,
    media_type: item.media_type,
    title: item.title,
    year: null,
    season: null,
    episode: null,
    episode_title: null,
    path: item.media_path,
    parent_external_id:
      !isMovie && item.bazarr_series_id != null ? `series:${item.bazarr_series_id}` : null,
    bazarr_movie_id: item.bazarr_movie_id,
    bazarr_series_id: item.bazarr_series_id,
    bazarr_episode_id: item.bazarr_episode_id,
  }
}

function openRequestSubtitles(item: Candidate) {
  requestModalMedia.value = candidateToMediaRef(item)
  requestModalLanguage.value = item.target_language
  requestModalOpen.value = true
}

function isTargetDone(item: Candidate) {
  return item.reason_code === 'target_exists'
}

function parseFilter(value: unknown): CandidateFilter | null {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw !== 'string') return null
  return FILTER_VALUES.includes(raw as CandidateFilter) ? (raw as CandidateFilter) : null
}

function syncFilterFromRoute() {
  categoryFilter.value = parseFilter(route.query.filter)
}

function setCategoryFilter(filter: CandidateFilter | null) {
  categoryFilter.value = filter
  const query = { ...route.query }
  if (filter) query.filter = filter
  else delete query.filter
  router.replace({ query })
}

function toggleCategoryFilter(filter: CandidateFilter) {
  setCategoryFilter(categoryFilter.value === filter ? null : filter)
}

function canRequestSource(item: Candidate) {
  if (isTargetDone(item)) return false
  if (item.active_request_job_id != null) return true
  if (item.source_subtitle_path) return false
  if (item.media_type === 'movie') return item.bazarr_movie_id != null
  return item.bazarr_episode_id != null && item.bazarr_series_id != null
}

function matchesOpenFilter(item: Candidate, filter: CandidateFilter) {
  if (filter === 'ready') return item.can_translate && !item.active_translate_job_id
  if (filter === 'extract') return item.can_extract && !item.active_extract_job_id
  if (filter === 'need-source') return canRequestSource(item) && !item.active_request_job_id
  return false
}

const openCandidates = computed(() => store.candidates.filter((item) => !isTargetDone(item)))

const doneCandidates = computed(() => store.candidates.filter((item) => isTargetDone(item)))

const pipelineCounts = computed(() => {
  const open = openCandidates.value
  return {
    ready: open.filter((item) => item.can_translate && !item.active_translate_job_id).length,
    extract: open.filter((item) => item.can_extract && !item.active_extract_job_id).length,
    needSource: open.filter((item) => canRequestSource(item) && !item.active_request_job_id).length,
    done: doneCandidates.value.length,
  }
})

const allFilterCards = computed(() => [
  { label: 'Ready to translate', filter: 'ready' as const, count: pipelineCounts.value.ready },
  { label: 'Can extract', filter: 'extract' as const, count: pipelineCounts.value.extract },
  { label: 'Need source', filter: 'need-source' as const, count: pipelineCounts.value.needSource },
  { label: 'Target exists', filter: 'target-exists' as const, count: pipelineCounts.value.done },
])

const filterCards = computed(() => allFilterCards.value.filter((item) => item.count > 0))

const showRequestAll = computed(
  () => pipelineCounts.value.needSource > 0 || batchBusy.value === 'request',
)
const showExtractAll = computed(
  () => pipelineCounts.value.extract > 0 || batchBusy.value === 'extract',
)
const showTranslateAll = computed(
  () => pipelineCounts.value.ready > 0 || batchBusy.value === 'translate',
)
const showBulkActions = computed(
  () => showRequestAll.value || showExtractAll.value || showTranslateAll.value,
)

const filteredOpenCandidates = computed(() => {
  const filter = categoryFilter.value
  if (!filter) return openCandidates.value
  if (filter === 'target-exists') return []
  return openCandidates.value.filter((item) => matchesOpenFilter(item, filter))
})

const showOpenList = computed(() => categoryFilter.value !== 'target-exists')

const showDoneAsMain = computed(() => categoryFilter.value === 'target-exists')

const showDoneSection = computed(
  () => !categoryFilter.value && doneCandidates.value.length > 0,
)

const selectedCandidates = computed(() =>
  filteredOpenCandidates.value.filter((item) => selectedKeys.value.has(item.key)),
)

const selectedCount = computed(() => selectedCandidates.value.length)

const allSelected = computed(
  () =>
    filteredOpenCandidates.value.length > 0 &&
    filteredOpenCandidates.value.every((item) => selectedKeys.value.has(item.key)),
)

const selectedRequestable = computed(() =>
  selectedCandidates.value.filter((item) => canRequestSource(item) && !item.active_request_job_id),
)

const selectedExtractable = computed(() =>
  selectedCandidates.value.filter((item) => item.can_extract && !item.active_extract_job_id),
)

const selectedTranslatable = computed(() =>
  selectedCandidates.value.filter((item) => item.can_translate && !item.active_translate_job_id),
)

const emptyOpenMessage = computed(() => {
  if (categoryFilter.value && categoryFilter.value !== 'target-exists') {
    const card = allFilterCards.value.find((item) => item.filter === categoryFilter.value)
    return card ? `No candidates in “${card.label}”.` : 'No candidates match this filter.'
  }
  if (doneCandidates.value.length) {
    return 'No open candidates. Finished items are listed below.'
  }
  return 'No candidates yet. Configure Bazarr in Settings, then refresh.'
})

onMounted(() => {
  syncFilterFromRoute()
  store.loadSettings().catch(() => undefined)
  store.loadCandidates().catch(() => undefined)
})

watch(
  () => route.query.filter,
  () => {
    syncFilterFromRoute()
    pruneSelectedKeys()
  },
)

watch(allFilterCards, (cards) => {
  const active = categoryFilter.value
  if (!active) return
  const card = cards.find((item) => item.filter === active)
  if (!card || card.count === 0) {
    setCategoryFilter(null)
  }
})

function summarizeBatch(result: BatchJobsResult, action: string) {
  const parts = [`${action}: queued ${result.created_count}`]
  if (result.reused_count) parts.push(`reused ${result.reused_count}`)
  if (result.skipped_count) parts.push(`skipped ${result.skipped_count}`)
  if (result.errors.length) parts.push(`${result.errors.length} error(s)`)
  return parts.join(', ')
}

async function refresh() {
  actionError.value = null
  actionInfo.value = null
  try {
    await store.loadCandidates()
    pruneSelectedKeys()
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  }
}

function pruneSelectedKeys() {
  const valid = new Set(filteredOpenCandidates.value.map((item) => item.key))
  const next = new Set<string>()
  for (const key of selectedKeys.value) {
    if (valid.has(key)) next.add(key)
  }
  selectedKeys.value = next
}

function isSelected(key: string) {
  return selectedKeys.value.has(key)
}

function toggleSelected(key: string, checked: boolean) {
  const next = new Set(selectedKeys.value)
  if (checked) next.add(key)
  else next.delete(key)
  selectedKeys.value = next
}

function onRowCheckboxChange(key: string, event: Event) {
  const target = event.target as HTMLInputElement
  toggleSelected(key, target.checked)
}

function toggleAllSelected(checked: boolean) {
  selectedKeys.value = checked
    ? new Set(filteredOpenCandidates.value.map((item) => item.key))
    : new Set()
}

function onToggleAllSelected(event: Event) {
  const target = event.target as HTMLInputElement
  toggleAllSelected(target.checked)
}

async function runSelected(
  action: 'request' | 'extract' | 'translate',
  items: Candidate[],
  label: string,
  runOne: (item: Candidate) => Promise<unknown>,
) {
  if (!items.length) return
  batchBusy.value = action
  actionError.value = null
  actionInfo.value = null
  let created = 0
  const errors: string[] = []
  try {
    for (const item of items) {
      try {
        await runOne(item)
        created += 1
      } catch (err) {
        errors.push(`${item.title}: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
    selectedKeys.value = new Set()
    actionInfo.value = `${label}: queued ${created}`
    if (errors.length) {
      actionError.value = errors.slice(0, 5).join(' · ')
    }
    try {
      await store.loadCandidates()
    } catch {
      /* keep action result even if refresh fails */
    }
    if (created > 0) {
      await router.push('/jobs')
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    batchBusy.value = null
  }
}

async function requestMultiple() {
  await runSelected(
    'request',
    selectedRequestable.value,
    'Request source',
    (item) => store.requestSubtitle(item.key),
  )
}

async function extractMultiple() {
  await runSelected(
    'extract',
    selectedExtractable.value,
    'Extract',
    (item) => store.extractCandidate(item.key),
  )
}

async function translateMultiple() {
  await runSelected(
    'translate',
    selectedTranslatable.value,
    'Translate',
    (item) => store.translateCandidate(item.key),
  )
}

async function requestAllMissing() {
  if (categoryFilter.value) {
    await runSelected(
      'request',
      filteredOpenCandidates.value.filter(
        (item) => canRequestSource(item) && !item.active_request_job_id,
      ),
      'Request source',
      (item) => store.requestSubtitle(item.key),
    )
    return
  }
  batchBusy.value = 'request'
  actionError.value = null
  actionInfo.value = null
  try {
    const result = await store.batchRequestSubtitles()
    actionInfo.value = summarizeBatch(result, 'Request source')
    if (result.errors.length) {
      actionError.value = result.errors.slice(0, 5).join(' · ')
    }
    if (result.created_count + result.reused_count > 0) {
      await router.push('/jobs')
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    batchBusy.value = null
  }
}

async function extractAll() {
  if (categoryFilter.value) {
    await runSelected(
      'extract',
      filteredOpenCandidates.value.filter(
        (item) => item.can_extract && !item.active_extract_job_id,
      ),
      'Extract',
      (item) => store.extractCandidate(item.key),
    )
    return
  }
  batchBusy.value = 'extract'
  actionError.value = null
  actionInfo.value = null
  try {
    const result = await store.batchExtract()
    actionInfo.value = summarizeBatch(result, 'Extract')
    if (result.errors.length) {
      actionError.value = result.errors.slice(0, 5).join(' · ')
    }
    if (result.created_count + result.reused_count > 0) {
      await router.push('/jobs')
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    batchBusy.value = null
  }
}

async function translateAll() {
  if (categoryFilter.value) {
    await runSelected(
      'translate',
      filteredOpenCandidates.value.filter(
        (item) => item.can_translate && !item.active_translate_job_id,
      ),
      'Translate',
      (item) => store.translateCandidate(item.key),
    )
    return
  }
  batchBusy.value = 'translate'
  actionError.value = null
  actionInfo.value = null
  try {
    const result = await store.batchTranslate()
    actionInfo.value = summarizeBatch(result, 'Translate')
    if (result.errors.length) {
      actionError.value = result.errors.slice(0, 5).join(' · ')
    }
    if (result.created_count + result.reused_count > 0) {
      await router.push('/jobs')
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    batchBusy.value = null
  }
}

async function translate(key: string) {
  translatingKey.value = key
  actionError.value = null
  actionInfo.value = null
  try {
    const job = await store.translateCandidate(key)
    await router.push(`/jobs/${job.id}`)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    translatingKey.value = null
  }
}

async function extract(item: Candidate) {
  extractingKey.value = item.key
  actionError.value = null
  actionInfo.value = null
  try {
    const job = await store.extractCandidate(item.key)
    // Background job — open detail so progress is visible, then refresh list when done later.
    await router.push(`/jobs/${job.id}`)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    extractingKey.value = null
  }
}

async function requestSource(item: Candidate) {
  requestingKey.value = item.key
  actionError.value = null
  actionInfo.value = null
  try {
    const job = await store.requestSubtitle(item.key)
    await router.push(`/jobs/${job.id}`)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    requestingKey.value = null
  }
}

function viewLogs(item: Candidate) {
  if (item.latest_job_id == null) return
  router.push(`/jobs/${item.latest_job_id}`)
}

function extractLabel(item: Candidate) {
  if (item.active_extract_job_id) return 'Extracting…'
  if (extractingKey.value === item.key) return 'Starting…'
  return 'Extract'
}

function canShowExtract(item: Candidate) {
  if (isTargetDone(item)) return false
  return item.can_extract || item.active_extract_job_id != null
}

function requestLabel(item: Candidate) {
  if (item.active_request_job_id) return 'Searching…'
  if (requestingKey.value === item.key) return 'Starting…'
  return 'Request source'
}

function translateLabel(item: Candidate) {
  if (item.active_translate_job_id) return 'Translating…'
  if (translatingKey.value === item.key) return 'Starting…'
  return 'Translate'
}

function statusText(item: Candidate) {
  if (item.active_translate_job_id) return 'Translating…'
  if (item.can_translate) return 'Ready'
  if (isTargetDone(item)) return 'Target already exists'
  return item.reason || item.reason_code || 'Unavailable'
}
</script>

<template>
  <section class="space-y-6">
    <div class="space-y-4">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <h1 class="font-display text-2xl font-bold text-ink-900 sm:text-3xl dark:text-ink-50">Candidates</h1>
          <p class="mt-1 max-w-2xl text-sm text-ink-600 sm:text-base dark:text-ink-300">
            Movies and episodes missing your target subtitle.
          </p>
        </div>
        <div class="flex shrink-0 flex-col items-end gap-2">
          <button
            class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-accent/90 disabled:opacity-60"
            type="button"
            :disabled="store.loading || batchBusy != null"
            @click="refresh"
          >
            {{ store.loading ? 'Refreshing…' : 'Refresh' }}
          </button>
          <div v-if="showBulkActions" class="flex flex-wrap justify-end gap-2">
            <button
              v-if="showRequestAll"
              class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
              type="button"
              :disabled="store.loading || batchBusy != null"
              @click="requestAllMissing"
            >
              {{ batchBusy === 'request' ? 'Requesting source…' : 'Request all sources' }}
            </button>
            <button
              v-if="showExtractAll"
              class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
              type="button"
              :disabled="store.loading || batchBusy != null"
              @click="extractAll"
            >
              {{ batchBusy === 'extract' ? 'Queuing…' : 'Extract all' }}
            </button>
            <button
              v-if="showTranslateAll"
              class="rounded-md border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
              type="button"
              :disabled="store.loading || batchBusy != null"
              @click="translateAll"
            >
              {{ batchBusy === 'translate' ? 'Queuing…' : 'Translate all' }}
            </button>
          </div>
        </div>
      </div>
      <div v-if="selectedCount" class="flex flex-wrap gap-2">
        <button
          v-if="selectedRequestable.length"
          class="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-60"
          type="button"
          :disabled="store.loading || batchBusy != null"
          @click="requestMultiple"
        >
          {{
            batchBusy === 'request'
              ? 'Requesting source…'
              : `Request multiple sources (${selectedRequestable.length})`
          }}
        </button>
        <button
          v-if="selectedExtractable.length"
          class="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-60"
          type="button"
          :disabled="store.loading || batchBusy != null"
          @click="extractMultiple"
        >
          {{ batchBusy === 'extract' ? 'Queuing…' : `Extract multiple (${selectedExtractable.length})` }}
        </button>
        <button
          v-if="selectedTranslatable.length"
          class="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-60"
          type="button"
          :disabled="store.loading || batchBusy != null"
          @click="translateMultiple"
        >
          {{ batchBusy === 'translate' ? 'Queuing…' : `Translate multiple (${selectedTranslatable.length})` }}
        </button>
      </div>
    </div>

    <div v-if="filterCards.length" class="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
      <button
        v-for="item in filterCards"
        :key="item.filter"
        type="button"
        class="rounded-xl border px-3 py-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:px-4"
        :class="
          categoryFilter === item.filter
            ? 'border-accent bg-accent/10 dark:bg-accent/20'
            : 'border-ink-200 bg-white/80 hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60 dark:hover:border-accent/50'
        "
        :aria-pressed="categoryFilter === item.filter"
        @click="toggleCategoryFilter(item.filter)"
      >
        <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">{{ item.label }}</div>
        <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ item.count }}</div>
      </button>
    </div>

    <p v-if="actionError || store.error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
      {{ actionError || store.error }}
    </p>
    <p v-else-if="actionInfo" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
      {{ actionInfo }}
    </p>

    <!-- Mobile / tablet card list -->
    <div v-if="showOpenList" class="space-y-3 lg:hidden">
      <p v-if="!filteredOpenCandidates.length" class="rounded-xl border border-ink-200 bg-white/80 px-4 py-8 text-sm text-ink-500 dark:border-ink-800 dark:bg-ink-900/60">
        {{ emptyOpenMessage }}
      </p>
      <article
        v-for="item in filteredOpenCandidates"
        :key="`card-${item.key}`"
        class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="flex items-start gap-3">
          <input
            class="mt-1"
            type="checkbox"
            :checked="isSelected(item.key)"
            :aria-label="`Select ${item.title}`"
            @change="onRowCheckboxChange(item.key, $event)"
          />
          <div class="min-w-0 flex-1">
            <h2 class="font-medium leading-snug text-ink-900 dark:text-ink-50">{{ item.title }}</h2>
            <p class="mt-1 break-all text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</p>
          </div>
        </div>

        <dl class="mt-3 text-xs">
          <div>
            <dt class="text-ink-500">Status</dt>
            <dd
              class="break-words"
              :class="
                item.active_translate_job_id
                  ? 'text-accent'
                  : item.can_translate
                    ? 'text-emerald-700 dark:text-emerald-300'
                    : 'text-ink-500'
              "
            >
              {{ statusText(item) }}
            </dd>
          </div>
        </dl>

        <div v-if="item.has_embedded" class="mt-3 flex flex-wrap gap-1.5">
          <span
            v-for="(track, idx) in item.embedded_subtitles"
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

        <div class="mt-4 flex flex-wrap gap-2">
          <button
            class="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
            type="button"
            @click="openRequestSubtitles(item)"
          >
            Request subtitles
          </button>
          <button
            v-if="item.latest_job_id != null"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            @click="viewLogs(item)"
          >
            View logs
          </button>
          <button
            v-if="canRequestSource(item)"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            :disabled="requestingKey === item.key || item.active_request_job_id != null || batchBusy != null"
            @click="requestSource(item)"
          >
            {{ requestLabel(item) }}
          </button>
          <button
            v-if="canShowExtract(item)"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            :disabled="!item.can_extract || extractingKey === item.key || item.active_extract_job_id != null || batchBusy != null"
            @click="extract(item)"
          >
            {{ extractLabel(item) }}
          </button>
          <button
            class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            :disabled="!item.can_translate || translatingKey === item.key || item.active_translate_job_id != null || batchBusy != null"
            @click="translate(item.key)"
          >
            {{ translateLabel(item) }}
          </button>
        </div>
      </article>
    </div>

    <!-- Desktop table -->
    <div
      v-if="showOpenList"
      class="hidden overflow-x-auto rounded-xl border border-ink-200 bg-white/80 lg:block dark:border-ink-800 dark:bg-ink-900/60"
    >
      <table class="w-full min-w-[40rem] table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[50%]" />
          <col class="w-[20%]" />
          <col class="w-[30%]" />
        </colgroup>
        <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
          <tr>
            <th class="px-4 py-3 font-medium">
              <label class="inline-flex items-center gap-2">
                <input
                  type="checkbox"
                  :checked="allSelected"
                  :disabled="!filteredOpenCandidates.length"
                  :indeterminate.prop="selectedCount > 0 && !allSelected"
                  aria-label="Select all candidates"
                  @change="onToggleAllSelected($event)"
                />
                Title
              </label>
            </th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="sticky right-0 bg-ink-50/95 px-4 py-3 font-medium dark:bg-ink-950/95">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filteredOpenCandidates.length">
            <td class="px-4 py-8 text-ink-500" colspan="3">
              {{ emptyOpenMessage }}
            </td>
          </tr>
          <tr
            v-for="item in filteredOpenCandidates"
            :key="item.key"
            class="border-t border-ink-100 dark:border-ink-800"
          >
            <td class="px-4 py-3 align-top">
              <div class="flex items-start gap-3">
                <input
                  class="mt-0.5"
                  type="checkbox"
                  :checked="isSelected(item.key)"
                  :aria-label="`Select ${item.title}`"
                  @change="onRowCheckboxChange(item.key, $event)"
                />
                <div class="min-w-0 flex-1">
                  <div class="font-medium text-ink-900 dark:text-ink-50">{{ item.title }}</div>
                  <div class="mt-0.5 truncate text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</div>
                  <div v-if="item.has_embedded" class="mt-2 flex flex-wrap gap-1.5">
                    <span
                      v-for="(track, idx) in item.embedded_subtitles"
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
              </div>
            </td>
            <td class="px-4 py-3 align-top">
              <span
                v-if="item.active_translate_job_id"
                class="text-accent"
              >Translating…</span>
              <span v-else-if="item.can_translate" class="text-emerald-700 dark:text-emerald-300">Ready</span>
              <span v-else class="text-ink-500">{{ statusText(item) }}</span>
            </td>
            <td
              class="sticky right-0 bg-white/95 px-4 py-3 align-top shadow-[-8px_0_8px_-8px_rgba(0,0,0,0.25)] dark:bg-ink-900/95"
            >
              <div class="flex flex-wrap items-center justify-end gap-2">
                <button
                  class="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
                  type="button"
                  @click="openRequestSubtitles(item)"
                >
                  Request subtitles
                </button>
                <button
                  v-if="item.latest_job_id != null"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  @click="viewLogs(item)"
                >
                  View logs
                </button>
                <button
                  v-if="canRequestSource(item)"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="requestingKey === item.key || item.active_request_job_id != null || batchBusy != null"
                  @click="requestSource(item)"
                >
                  {{ requestLabel(item) }}
                </button>
                <button
                  v-if="canShowExtract(item)"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="!item.can_extract || extractingKey === item.key || item.active_extract_job_id != null || batchBusy != null"
                  @click="extract(item)"
                >
                  {{ extractLabel(item) }}
                </button>
                <button
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  :disabled="!item.can_translate || translatingKey === item.key || item.active_translate_job_id != null || batchBusy != null"
                  @click="translate(item.key)"
                >
                  {{ translateLabel(item) }}
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Target exists filter: primary list -->
    <div v-if="showDoneAsMain" class="space-y-3 lg:hidden">
      <p
        v-if="!doneCandidates.length"
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-8 text-sm text-ink-500 dark:border-ink-800 dark:bg-ink-900/60"
      >
        No candidates with target subtitles yet.
      </p>
      <article
        v-for="item in doneCandidates"
        :key="`done-main-card-${item.key}`"
        class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="min-w-0">
          <h2 class="font-medium leading-snug text-ink-900 dark:text-ink-50">{{ item.title }}</h2>
          <p class="mt-1 break-all text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</p>
        </div>
        <p class="mt-2 text-xs text-ink-500">{{ statusText(item) }}</p>
        <button
          v-if="item.latest_job_id != null"
          class="mt-3 rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
          type="button"
          @click="viewLogs(item)"
        >
          View logs
        </button>
      </article>
    </div>

    <div
      v-if="showDoneAsMain"
      class="hidden overflow-x-auto rounded-xl border border-ink-200 bg-white/80 lg:block dark:border-ink-800 dark:bg-ink-900/60"
    >
      <table class="w-full min-w-[32rem] table-fixed text-left text-sm">
        <colgroup>
          <col class="w-[55%]" />
          <col class="w-[25%]" />
          <col class="w-[20%]" />
        </colgroup>
        <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
          <tr>
            <th class="px-4 py-3 font-medium">Title</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!doneCandidates.length">
            <td class="px-4 py-8 text-ink-500" colspan="3">
              No candidates with target subtitles yet.
            </td>
          </tr>
          <tr
            v-for="item in doneCandidates"
            :key="`done-main-${item.key}`"
            class="border-t border-ink-100 dark:border-ink-800"
          >
            <td class="px-4 py-3 align-top">
              <div class="font-medium text-ink-900 dark:text-ink-50">{{ item.title }}</div>
              <div class="mt-0.5 truncate text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</div>
            </td>
            <td class="px-4 py-3 align-top text-ink-500">{{ statusText(item) }}</td>
            <td class="px-4 py-3 align-top">
              <button
                v-if="item.latest_job_id != null"
                class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                type="button"
                @click="viewLogs(item)"
              >
                View logs
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <details
      v-if="showDoneSection"
      class="group rounded-xl border border-ink-200 bg-white/60 dark:border-ink-800 dark:bg-ink-900/40"
    >
      <summary
        class="cursor-pointer list-none px-4 py-3 text-sm font-medium text-ink-600 marker:content-none dark:text-ink-300 [&::-webkit-details-marker]:hidden"
      >
        <span class="inline-flex items-center gap-2">
          <span class="text-ink-400 transition group-open:rotate-90" aria-hidden="true">▸</span>
          Target already exists ({{ doneCandidates.length }})
        </span>
      </summary>
      <div class="space-y-3 border-t border-ink-200 p-3 lg:hidden dark:border-ink-800">
        <article
          v-for="item in doneCandidates"
          :key="`done-card-${item.key}`"
          class="rounded-lg border border-ink-100 bg-ink-50/50 p-3 dark:border-ink-800 dark:bg-ink-950/30"
        >
          <div class="min-w-0">
            <h2 class="font-medium leading-snug text-ink-800 dark:text-ink-100">{{ item.title }}</h2>
            <p class="mt-1 break-all text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</p>
          </div>
          <p class="mt-2 text-xs text-ink-500">{{ statusText(item) }}</p>
          <button
            v-if="item.latest_job_id != null"
            class="mt-3 rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
            type="button"
            @click="viewLogs(item)"
          >
            View logs
          </button>
        </article>
      </div>
      <div class="hidden border-t border-ink-200 lg:block dark:border-ink-800">
        <table class="w-full min-w-[32rem] table-fixed text-left text-sm">
          <colgroup>
            <col class="w-[55%]" />
            <col class="w-[25%]" />
            <col class="w-[20%]" />
          </colgroup>
          <thead class="bg-ink-50/60 text-ink-500 dark:bg-ink-950/40 dark:text-ink-400">
            <tr>
              <th class="px-4 py-2 font-medium">Title</th>
              <th class="px-4 py-2 font-medium">Status</th>
              <th class="px-4 py-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in doneCandidates"
              :key="`done-${item.key}`"
              class="border-t border-ink-100 dark:border-ink-800"
            >
              <td class="px-4 py-2.5 align-top">
                <div class="font-medium text-ink-800 dark:text-ink-100">{{ item.title }}</div>
                <div class="mt-0.5 truncate text-xs text-ink-500" :title="item.media_path">{{ item.media_path }}</div>
              </td>
              <td class="px-4 py-2.5 align-top text-ink-500">{{ statusText(item) }}</td>
              <td class="px-4 py-2.5 align-top">
                <button
                  v-if="item.latest_job_id != null"
                  class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
                  type="button"
                  @click="viewLogs(item)"
                >
                  View logs
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>

    <RequestSubtitlesModal
      :open="requestModalOpen"
      :initial-media="requestModalMedia"
      :initial-language="requestModalLanguage"
      @close="requestModalOpen = false"
    />
  </section>
</template>
