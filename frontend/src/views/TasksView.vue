<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import RequestSubtitlesModal from '../components/RequestSubtitlesModal.vue'
import { api } from '../services/api'
import type { LocalizationTask } from '../types'
import { formatDateTime } from '../utils/datetime'

const tasks = ref<LocalizationTask[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 25
const loading = ref(false)
const error = ref<string | null>(null)
const statusFilter = ref<string | null>(null)
const originFilter = ref<string | null>(null)
const languageFilter = ref('')
const capabilityFilter = ref<string | null>(null)
const mediaTypeFilter = ref<string | null>(null)
const modalOpen = ref(false)
let timer: number | undefined

const filters = [
  { label: 'All', status: null },
  { label: 'Processing', status: 'processing' },
  { label: 'Waiting', status: 'waiting_for_source' },
  { label: 'Completed', status: 'completed' },
  { label: 'Failed', status: 'failed' },
]

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function load() {
  loading.value = true
  error.value = null
  try {
    const result = await api.getLocalizationTasksPage({
      status: statusFilter.value || undefined,
      origin: originFilter.value || undefined,
      language: languageFilter.value.trim() || undefined,
      capability: capabilityFilter.value || undefined,
      media_type: mediaTypeFilter.value || undefined,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    })
    tasks.value = result.items
    total.value = result.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

function applyStatus(status: string | null) {
  statusFilter.value = status
  page.value = 1
  load()
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    requested: 'Requested',
    planning: 'Planning',
    waiting_for_source: 'Waiting for source',
    processing: 'Processing',
    verifying: 'Verifying',
    completed: 'Completed',
    failed: 'Failed',
    blocked: 'Blocked',
    cancelled: 'Cancelled',
  }
  return map[status] || status
}

const empty = computed(() => !loading.value && !tasks.value.length)

onMounted(async () => {
  await load()
  timer = window.setInterval(() => {
    load().catch(() => undefined)
  }, 4000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="font-display text-2xl font-bold sm:text-3xl">Tasks</h1>
        <p class="mt-1 text-sm text-ink-600 sm:text-base dark:text-ink-300">
          Localization goals for movies and episodes.
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

    <div class="flex flex-wrap gap-2">
      <button
        v-for="f in filters"
        :key="f.label"
        type="button"
        class="rounded-full px-3 py-1 text-xs font-semibold"
        :class="
          statusFilter === f.status
            ? 'bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-900'
            : 'bg-ink-100 text-ink-700 dark:bg-ink-800 dark:text-ink-200'
        "
        @click="applyStatus(f.status)"
      >
        {{ f.label }}
      </button>
      <button
        type="button"
        class="rounded-full px-3 py-1 text-xs font-semibold"
        :class="
          originFilter === 'manual'
            ? 'bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-900'
            : 'bg-ink-100 text-ink-700 dark:bg-ink-800 dark:text-ink-200'
        "
        @click="originFilter = originFilter === 'manual' ? null : 'manual'; page = 1; load()"
      >
        Manual
      </button>
      <button
        type="button"
        class="rounded-full px-3 py-1 text-xs font-semibold"
        :class="
          originFilter === 'automatic'
            ? 'bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-900'
            : 'bg-ink-100 text-ink-700 dark:bg-ink-800 dark:text-ink-200'
        "
        @click="originFilter = originFilter === 'automatic' ? null : 'automatic'; page = 1; load()"
      >
        Automatic
      </button>
      <button
        type="button"
        class="rounded-full px-3 py-1 text-xs font-semibold"
        :class="
          capabilityFilter === 'subtitles'
            ? 'bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-900'
            : 'bg-ink-100 text-ink-700 dark:bg-ink-800 dark:text-ink-200'
        "
        @click="capabilityFilter = capabilityFilter === 'subtitles' ? null : 'subtitles'; page = 1; load()"
      >
        Subtitles
      </button>
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
        @click="mediaTypeFilter = mediaTypeFilter === mt ? null : mt; page = 1; load()"
      >
        {{ mt }}
      </button>
      <input
        v-model="languageFilter"
        type="search"
        class="rounded-full border border-ink-200 bg-white px-3 py-1 text-xs dark:border-ink-700 dark:bg-ink-900"
        placeholder="Language code…"
        @change="page = 1; load()"
      />
    </div>

    <p v-if="error" class="text-sm text-red-600">{{ error }}</p>

    <div
      v-if="empty"
      class="rounded-xl border border-dashed border-ink-300 bg-ink-50/80 p-8 text-center dark:border-ink-700 dark:bg-ink-900/40"
    >
      <p class="font-medium">No localization tasks yet.</p>
      <p class="mt-1 text-sm text-ink-500">
        Select a movie or episode and request subtitles in your preferred language.
      </p>
      <button
        type="button"
        class="mt-4 rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white"
        @click="modalOpen = true"
      >
        Request subtitles
      </button>
    </div>

    <div v-else class="overflow-hidden rounded-xl border border-ink-200 dark:border-ink-700">
      <table class="min-w-full divide-y divide-ink-200 text-sm dark:divide-ink-700">
        <thead class="bg-ink-50 text-left text-xs uppercase tracking-wide text-ink-500 dark:bg-ink-800">
          <tr>
            <th class="px-3 py-2">Media</th>
            <th class="px-3 py-2">Target</th>
            <th class="hidden px-3 py-2 sm:table-cell">Capability</th>
            <th class="px-3 py-2">Status</th>
            <th class="hidden px-3 py-2 md:table-cell">Origin</th>
            <th class="hidden px-3 py-2 lg:table-cell">Created</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-ink-100 dark:divide-ink-800">
          <tr v-for="task in tasks" :key="task.id" class="hover:bg-ink-50/80 dark:hover:bg-ink-800/50">
            <td class="px-3 py-2">
              <RouterLink class="font-medium text-accent hover:underline" :to="`/tasks/${task.id}`">
                {{ task.media_title || `Media #${task.media_item_id}` }}
              </RouterLink>
              <p class="text-xs text-ink-500">{{ task.media_type }}</p>
            </td>
            <td class="px-3 py-2">
              <span class="font-medium">{{ task.target_language_name }}</span>
              <p class="text-xs text-ink-500">{{ task.target_language_code }}</p>
            </td>
            <td class="hidden px-3 py-2 capitalize sm:table-cell">{{ task.capability }}</td>
            <td class="px-3 py-2">
              <span class="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-semibold dark:bg-ink-800">
                {{ statusLabel(task.status) }}
              </span>
            </td>
            <td class="hidden px-3 py-2 capitalize md:table-cell">{{ task.origin }}</td>
            <td class="hidden px-3 py-2 text-ink-500 lg:table-cell">
              {{ formatDateTime(task.created_at) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > pageSize" class="flex items-center justify-between text-sm">
      <p class="text-ink-500">{{ total }} tasks</p>
      <div class="flex gap-2">
        <button
          type="button"
          class="rounded-md border border-ink-300 px-3 py-1 dark:border-ink-600"
          :disabled="page <= 1"
          @click="page -= 1; load()"
        >
          Previous
        </button>
        <span class="px-2 py-1 text-ink-500">{{ page }} / {{ pageCount }}</span>
        <button
          type="button"
          class="rounded-md border border-ink-300 px-3 py-1 dark:border-ink-600"
          :disabled="page >= pageCount"
          @click="page += 1; load()"
        >
          Next
        </button>
      </div>
    </div>

    <p class="text-xs text-ink-500">
      Low-level execution history remains available under
      <RouterLink class="text-accent hover:underline" to="/jobs">Jobs</RouterLink>.
    </p>

    <RequestSubtitlesModal :open="modalOpen" @close="modalOpen = false" @created="load" />
  </section>
</template>
