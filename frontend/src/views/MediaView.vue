<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { Candidate, LocalizationTask, MediaItem, MediaRef } from '../types'
import {
  candidateExternalId,
  candidateToMediaRef,
  mediaHref,
} from '../utils/mediaNav'
import { isActiveTaskStatus, languageChipClass, taskStatusLabel } from '../utils/status'

type MediaFilter = 'all' | 'needs-work' | 'in-progress' | 'failed' | 'completed'
type RowKind = 'in-progress' | 'needs-work' | 'failed' | 'completed' | 'idle'

const FILTER_VALUES: MediaFilter[] = ['all', 'needs-work', 'in-progress', 'failed', 'completed']

interface LanguageChip {
  code: string
  name: string
  status: string | null
  available: boolean
}

interface MediaRow {
  key: string
  mediaId: number | null
  title: string
  mediaType: string
  year: number | null
  path: string | null
  season: number | null
  episode: number | null
  candidate: Candidate | null
  tasks: LocalizationTask[]
  ref: MediaRef | null
}

const store = useAppStore()
const { mediaItems, localizationTasks, mediaListLoaded } = storeToRefs(store)
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const error = ref<string | null>(null)
const actionError = ref<string | null>(null)
const actionInfo = ref<string | null>(null)
const search = ref('')
const mediaTypeFilter = ref<string | null>(null)
const categoryFilter = ref<MediaFilter>('all')
const modalOpen = ref(false)
const requestMedia = ref<MediaRef | null>(null)
const requestLanguage = ref<string | null>(null)
const selectedKeys = ref<Set<string>>(new Set())
const localizing = ref(false)
const openingKey = ref<string | null>(null)
let timer: number | undefined

function parseFilter(value: unknown): MediaFilter {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw !== 'string') return 'all'
  if (raw === 'ready' || raw === 'extract' || raw === 'need-source') return 'needs-work'
  if (raw === 'target-exists') return 'completed'
  if (raw === 'processing') return 'in-progress'
  return FILTER_VALUES.includes(raw as MediaFilter) ? (raw as MediaFilter) : 'all'
}

function syncFilterFromRoute() {
  categoryFilter.value = parseFilter(route.query.filter)
}

function setCategoryFilter(filter: MediaFilter) {
  categoryFilter.value = filter
  const query = { ...route.query }
  if (filter === 'all') delete query.filter
  else query.filter = filter
  router.replace({ query })
}

function mediaRefFromItem(item: MediaItem): MediaRef {
  return {
    provider_id: item.provider_id,
    external_id: item.external_id,
    media_type: item.media_type as MediaRef['media_type'],
    title: item.title,
    year: item.year,
    season: item.season,
    episode: item.episode,
    episode_title: item.episode_title,
    path: item.path,
    parent_external_id: null,
    bazarr_movie_id: item.bazarr_movie_id,
    bazarr_series_id: item.bazarr_series_id,
    bazarr_episode_id: item.bazarr_episode_id,
  }
}

function emptyRow(partial: Partial<MediaRow> & Pick<MediaRow, 'key' | 'title'>): MediaRow {
  return {
    mediaId: null,
    mediaType: 'movie',
    year: null,
    path: null,
    season: null,
    episode: null,
    candidate: null,
    tasks: [],
    ref: null,
    ...partial,
  }
}

const rows = computed(() => {
  const byId = new Map<number, MediaRow>()
  const byExternal = new Map<string, MediaRow>()
  const byPath = new Map<string, MediaRow>()
  const extras: MediaRow[] = []

  function index(row: MediaRow) {
    if (row.mediaId != null) byId.set(row.mediaId, row)
    if (row.ref?.external_id) byExternal.set(row.ref.external_id, row)
    if (row.path) byPath.set(row.path, row)
  }

  for (const item of mediaItems.value) {
    const row = emptyRow({
      key: `media:${item.id}`,
      mediaId: item.id,
      title: item.title,
      mediaType: item.media_type,
      year: item.year,
      path: item.path,
      season: item.season,
      episode: item.episode,
      ref: mediaRefFromItem(item),
    })
    index(row)
  }

  for (const task of localizationTasks.value) {
    let row = byId.get(task.media_item_id)
    if (!row) {
      row = emptyRow({
        key: `media:${task.media_item_id}`,
        mediaId: task.media_item_id,
        title: task.media_title || `Media #${task.media_item_id}`,
        mediaType: task.media_type || 'movie',
        year: task.media_year,
      })
      index(row)
    }
    row.tasks.push(task)
  }

  for (const candidate of store.candidates) {
    const external = candidateExternalId(candidate)
    const row =
      (external ? byExternal.get(external) : undefined) ||
      (candidate.media_path ? byPath.get(candidate.media_path) : undefined)
    if (row) {
      row.candidate = candidate
      if (!row.ref) row.ref = candidateToMediaRef(candidate)
      if (!row.path) row.path = candidate.media_path
      continue
    }
    const extra = emptyRow({
      key: `cand:${candidate.key}`,
      title: candidate.title,
      mediaType: candidate.media_type,
      path: candidate.media_path,
      candidate,
      ref: candidateToMediaRef(candidate),
    })
    extras.push(extra)
  }

  return [...byId.values(), ...extras]
})

function rowKind(row: MediaRow): RowKind {
  if (row.tasks.some((task) => isActiveTaskStatus(task.status))) return 'in-progress'
  if (row.tasks.some((task) => task.status === 'failed')) return 'failed'
  if (row.candidate && row.candidate.reason_code !== 'target_exists') return 'needs-work'
  if (
    row.tasks.some((task) => task.status === 'completed') ||
    row.candidate?.reason_code === 'target_exists'
  ) {
    return 'completed'
  }
  return 'idle'
}

function rowLanguages(row: MediaRow): LanguageChip[] {
  const map = new Map<string, LanguageChip>()
  for (const task of row.tasks) {
    map.set(task.target_language_code, {
      code: task.target_language_code,
      name: task.target_language_name,
      status: task.status,
      available: task.status === 'completed',
    })
  }
  if (row.candidate && !map.has(row.candidate.target_language)) {
    map.set(row.candidate.target_language, {
      code: row.candidate.target_language,
      name: row.candidate.target_language,
      status: row.candidate.reason_code === 'target_exists' ? 'completed' : null,
      available: row.candidate.reason_code === 'target_exists',
    })
  }
  return [...map.values()]
}

function rowMeta(row: MediaRow) {
  const parts: string[] = []
  if (row.year) parts.push(String(row.year))
  if (row.mediaType) parts.push(row.mediaType)
  if (row.mediaType === 'episode' && row.season != null && row.episode != null) {
    parts.push(`S${String(row.season).padStart(2, '0')}E${String(row.episode).padStart(2, '0')}`)
  }
  return parts.join(' · ')
}

const KIND_ORDER: Record<RowKind, number> = {
  'in-progress': 0,
  'needs-work': 1,
  failed: 2,
  completed: 3,
  idle: 4,
}

const filteredRows = computed(() => {
  const q = search.value.trim().toLowerCase()
  return rows.value
    .filter((row) => {
      if (mediaTypeFilter.value && row.mediaType !== mediaTypeFilter.value) return false
      const kind = rowKind(row)
      if (categoryFilter.value === 'needs-work' && kind !== 'needs-work') return false
      if (categoryFilter.value === 'in-progress' && kind !== 'in-progress') return false
      if (categoryFilter.value === 'failed' && kind !== 'failed') return false
      if (categoryFilter.value === 'completed' && kind !== 'completed') return false
      if (!q) return true
      return (
        row.title.toLowerCase().includes(q) ||
        (row.path || '').toLowerCase().includes(q)
      )
    })
    .sort((a, b) => {
      const kindDiff = KIND_ORDER[rowKind(a)] - KIND_ORDER[rowKind(b)]
      if (kindDiff !== 0) return kindDiff
      return a.title.localeCompare(b.title)
    })
})

const counts = computed(() => {
  const list = rows.value
  return {
    all: list.length,
    needsWork: list.filter((row) => rowKind(row) === 'needs-work').length,
    inProgress: list.filter((row) => rowKind(row) === 'in-progress').length,
    failed: list.filter((row) => rowKind(row) === 'failed').length,
    completed: list.filter((row) => rowKind(row) === 'completed').length,
  }
})

const filterCards = computed(() =>
  [
    { label: 'All', filter: 'all' as const, count: counts.value.all },
    { label: 'Needs work', filter: 'needs-work' as const, count: counts.value.needsWork },
    { label: 'In progress', filter: 'in-progress' as const, count: counts.value.inProgress },
    { label: 'Failed', filter: 'failed' as const, count: counts.value.failed },
    { label: 'Completed', filter: 'completed' as const, count: counts.value.completed },
  ].filter((item) => item.filter === 'all' || item.count > 0),
)

const localizableRows = computed(() =>
  filteredRows.value.filter((row) => rowKind(row) === 'needs-work' && row.ref),
)

const selectedRows = computed(() =>
  localizableRows.value.filter((row) => selectedKeys.value.has(row.key)),
)

const empty = computed(() => !loading.value && !filteredRows.value.length)

async function load(refreshCandidates = false, silent = false) {
  if (!silent) loading.value = true
  error.value = null
  try {
    const candidatePromise = refreshCandidates
      ? store.loadCandidates()
      : store.loadCandidatesCached().catch(() => store.loadCandidates())
    await Promise.all([store.loadMediaList(), candidatePromise])
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (!silent) loading.value = false
  }
}

async function openRow(row: MediaRow) {
  if (row.mediaId != null) {
    await router.push(mediaHref(row.mediaId))
    return
  }
  if (!row.ref) return
  openingKey.value = row.key
  actionError.value = null
  try {
    const media = await api.ensureMedia(row.ref)
    await router.push(mediaHref(media.id))
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    openingKey.value = null
  }
}

function openRequest(row?: MediaRow) {
  requestMedia.value = row?.ref ?? null
  requestLanguage.value = row?.candidate?.target_language ?? null
  modalOpen.value = true
}

function targetLanguageFor(row: MediaRow) {
  return (
    row.candidate?.target_language ||
    store.settings?.target_language.code ||
    'pt-PT'
  )
}

async function localizeRows(items: MediaRow[]) {
  if (!items.length || localizing.value) return
  localizing.value = true
  actionError.value = null
  actionInfo.value = null
  let created = 0
  const errors: string[] = []
  try {
    for (const row of items) {
      if (!row.ref) continue
      try {
        const media = row.mediaId
          ? { id: row.mediaId }
          : await api.ensureMedia(row.ref)
        await api.createLocalizationTask(media.id, {
          target_language: targetLanguageFor(row),
          capability: 'subtitles',
        })
        created += 1
      } catch (err) {
        const e = err as Error & { code?: string }
        if (e.code === 'active_task_exists') {
          created += 1
          continue
        }
        errors.push(`${row.title}: ${e.message || String(err)}`)
      }
    }
    selectedKeys.value = new Set()
    actionInfo.value = `Localize: queued ${created}`
    if (errors.length) actionError.value = errors.slice(0, 5).join(' · ')
    await load(false, mediaListLoaded.value)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    localizing.value = false
  }
}

function isSelected(key: string) {
  return selectedKeys.value.has(key)
}

function onRowCheckboxChange(key: string, event: Event) {
  const target = event.target as HTMLInputElement
  const next = new Set(selectedKeys.value)
  if (target.checked) next.add(key)
  else next.delete(key)
  selectedKeys.value = next
}

function pruneSelected() {
  const valid = new Set(localizableRows.value.map((row) => row.key))
  const next = new Set<string>()
  for (const key of selectedKeys.value) {
    if (valid.has(key)) next.add(key)
  }
  selectedKeys.value = next
}

watch(
  () => route.query.filter,
  () => {
    syncFilterFromRoute()
    pruneSelected()
  },
)

watch(filteredRows, pruneSelected)

onMounted(async () => {
  syncFilterFromRoute()
  await store.loadSettings().catch(() => undefined)
  await load(false, mediaListLoaded.value)
  timer = window.setInterval(() => {
    load(false, true).catch(() => undefined)
  }, 8000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="font-display text-2xl font-bold sm:text-3xl">Media</h1>
        <p class="mt-1 text-sm text-ink-600 sm:text-base dark:text-ink-300">
          Movies and episodes with localization history.
        </p>
      </div>
      <div class="flex flex-wrap justify-end gap-2">
        <button
          type="button"
          class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
          :disabled="loading || localizing"
          @click="load(true)"
        >
          {{ loading ? 'Refreshing…' : 'Refresh' }}
        </button>
        <button
          type="button"
          class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
          @click="openRequest()"
        >
          Request subtitles
        </button>
      </div>
    </div>

    <div v-if="filterCards.length" class="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-5">
      <button
        v-for="item in filterCards"
        :key="item.filter"
        type="button"
        class="rounded-xl border px-3 py-3 text-left transition sm:px-4"
        :class="
          categoryFilter === item.filter
            ? 'border-accent bg-accent/10 dark:bg-accent/20'
            : 'border-ink-200 bg-white/80 hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60'
        "
        :aria-pressed="categoryFilter === item.filter"
        @click="setCategoryFilter(item.filter)"
      >
        <div class="text-[10px] uppercase tracking-wide text-ink-500 sm:text-xs">{{ item.label }}</div>
        <div class="mt-1 font-display text-xl font-bold sm:text-2xl">{{ item.count }}</div>
      </button>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <button
        v-for="mt in ['movie', 'episode']"
        :key="mt"
        type="button"
        class="rounded-full px-3 py-1 text-xs font-semibold capitalize"
        :class="
          mediaTypeFilter === mt
            ? 'bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-900'
            : 'bg-ink-100 text-ink-700 dark:bg-ink-800 dark:text-ink-200'
        "
        @click="mediaTypeFilter = mediaTypeFilter === mt ? null : mt"
      >
        {{ mt }}
      </button>
      <input
        v-model="search"
        type="search"
        class="rounded-full border border-ink-200 bg-white px-3 py-1 text-xs dark:border-ink-700 dark:bg-ink-900"
        placeholder="Search titles…"
      />
      <button
        v-if="selectedRows.length"
        type="button"
        class="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
        :disabled="localizing"
        @click="localizeRows(selectedRows)"
      >
        {{ localizing ? 'Queuing…' : `Localize selected (${selectedRows.length})` }}
      </button>
      <button
        v-else-if="localizableRows.length && categoryFilter === 'needs-work'"
        type="button"
        class="rounded-md border border-ink-300 px-3 py-1.5 text-xs font-semibold dark:border-ink-600"
        :disabled="localizing"
        @click="localizeRows(localizableRows)"
      >
        {{ localizing ? 'Queuing…' : `Localize all missing (${localizableRows.length})` }}
      </button>
    </div>

    <p v-if="error || actionError" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
      {{ actionError || error }}
    </p>
    <p v-else-if="actionInfo" class="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
      {{ actionInfo }}
    </p>

    <div
      v-if="empty"
      class="rounded-xl border border-dashed border-ink-300 bg-ink-50/80 p-8 text-center dark:border-ink-700 dark:bg-ink-900/40"
    >
      <p class="font-medium">No media in this view.</p>
      <p class="mt-1 text-sm text-ink-500">
        Request subtitles for a title, or refresh Bazarr wanted items.
      </p>
      <button
        type="button"
        class="mt-4 rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
        @click="openRequest()"
      >
        Request subtitles
      </button>
    </div>

    <div v-else class="space-y-3">
      <article
        v-for="row in filteredRows"
        :key="row.key"
        class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="flex items-start gap-3">
          <input
            v-if="rowKind(row) === 'needs-work' && row.ref"
            class="mt-1"
            type="checkbox"
            :checked="isSelected(row.key)"
            :aria-label="`Select ${row.title}`"
            @change="onRowCheckboxChange(row.key, $event)"
          />
          <div class="min-w-0 flex-1">
            <button
              type="button"
              class="text-left font-medium text-accent hover:underline"
              :disabled="openingKey === row.key"
              @click="openRow(row)"
            >
              {{ row.title }}
            </button>
            <p v-if="rowMeta(row)" class="mt-0.5 text-xs capitalize text-ink-500">
              {{ rowMeta(row) }}
            </p>
            <p v-if="row.path" class="mt-0.5 truncate text-xs text-ink-500" :title="row.path">
              {{ row.path }}
            </p>
            <div v-if="rowLanguages(row).length" class="mt-3 flex flex-wrap gap-2">
              <span
                v-for="lang in rowLanguages(row)"
                :key="`${row.key}-${lang.code}`"
                class="rounded-full border px-2.5 py-0.5 text-xs font-semibold"
                :class="languageChipClass(lang.status, lang.available)"
              >
                {{ lang.name }}
                <span v-if="lang.status" class="font-normal opacity-80">
                  {{ taskStatusLabel(lang.status) }}
                </span>
              </span>
            </div>
          </div>
          <button
            v-if="rowKind(row) === 'needs-work'"
            type="button"
            class="shrink-0 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white"
            @click="openRequest(row)"
          >
            Localize
          </button>
        </div>
      </article>
    </div>

    <RequestSubtitlesModal
      :open="modalOpen"
      :initial-media="requestMedia"
      :initial-language="requestLanguage"
      @close="modalOpen = false"
      @created="load(false, mediaListLoaded)"
    />
  </section>
</template>
