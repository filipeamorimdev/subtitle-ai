<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api } from '../services/api'
import type { LocalizationTask } from '../types'
import { formatDateTime } from '../utils/datetime'
import { localizationTaskTitle, mediaHref } from '../utils/mediaNav'

const PAGE_SIZE = 20

const route = useRoute()
const router = useRouter()
const tasks = ref<LocalizationTask[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)

const page = computed(() => {
  const raw = Number(Array.isArray(route.query.page) ? route.query.page[0] : route.query.page)
  if (!Number.isFinite(raw) || raw < 1) return 1
  return Math.floor(raw)
})

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const rangeLabel = computed(() => {
  if (!total.value) return ''
  const start = (page.value - 1) * PAGE_SIZE + 1
  const end = Math.min(page.value * PAGE_SIZE, total.value)
  return `${start}–${end} of ${total.value}`
})

function setPage(next: number) {
  const clamped = Math.min(Math.max(1, next), totalPages.value)
  const query = { ...route.query }
  if (clamped <= 1) delete query.page
  else query.page = String(clamped)
  router.replace({ query })
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const result = await api.getLocalizationTasksPage({
      status: 'completed',
      sort: 'completed_at',
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    })
    tasks.value = result.items
    total.value = result.total
    if (page.value > totalPages.value) {
      setPage(totalPages.value)
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(page, load)
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p class="text-sm text-ink-500">
          <RouterLink class="text-accent hover:underline" to="/">← Dashboard</RouterLink>
        </p>
        <h1 class="mt-1 font-display text-2xl font-bold sm:text-3xl">Translations</h1>
      </div>
    </div>

    <p v-if="error" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
      {{ error }}
    </p>
    <p v-else-if="loading && !tasks.length" class="text-ink-500">Loading translations…</p>

    <div
      v-else-if="!tasks.length"
      class="rounded-xl border border-dashed border-ink-300 bg-ink-50/80 p-8 text-center dark:border-ink-700 dark:bg-ink-900/40"
    >
      <p class="font-medium">No completed translations yet.</p>
      <p class="mt-1 text-sm text-ink-500">Finished jobs will show up here, newest first.</p>
    </div>

    <template v-else>
      <div class="space-y-3 lg:hidden">
        <article
          v-for="task in tasks"
          :key="task.id"
          class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
        >
          <RouterLink class="font-medium text-accent hover:underline" :to="mediaHref(task.media_item_id)">
            {{ localizationTaskTitle(task) }}
          </RouterLink>
          <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
            <div>
              <dt class="text-ink-500">Language</dt>
              <dd>{{ task.target_language_name }}</dd>
            </div>
            <div>
              <dt class="text-ink-500">Origin</dt>
              <dd class="capitalize">{{ task.origin }}</dd>
            </div>
            <div class="col-span-2">
              <dt class="text-ink-500">Completed</dt>
              <dd>{{ formatDateTime(task.completed_at || task.updated_at) }}</dd>
            </div>
          </dl>
        </article>
      </div>

      <div class="hidden overflow-x-auto rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60 lg:block">
        <table class="min-w-full text-left text-sm">
          <thead class="border-b border-ink-200 text-ink-500 dark:border-ink-800 dark:text-ink-300">
            <tr>
              <th class="px-4 py-3 font-medium">Title</th>
              <th class="py-3 pr-4 font-medium">Language</th>
              <th class="py-3 pr-4 font-medium">Completed</th>
              <th class="py-3 pr-4 font-medium">Origin</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="task in tasks"
              :key="task.id"
              class="border-b border-ink-100 last:border-0 dark:border-ink-800/80"
            >
              <td class="px-4 py-3 align-top">
                <RouterLink class="font-medium text-accent hover:underline" :to="mediaHref(task.media_item_id)">
                  {{ localizationTaskTitle(task) }}
                </RouterLink>
                <div v-if="task.media_type" class="mt-0.5 text-xs capitalize text-ink-500">
                  {{ task.media_type }}
                </div>
              </td>
              <td class="py-3 pr-4 align-top">{{ task.target_language_name }}</td>
              <td class="py-3 pr-4 align-top whitespace-nowrap text-ink-600 dark:text-ink-300">
                {{ formatDateTime(task.completed_at || task.updated_at) }}
              </td>
              <td class="py-3 pr-4 align-top capitalize text-ink-600 dark:text-ink-300">
                {{ task.origin }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div
        v-if="totalPages > 1"
        class="flex flex-wrap items-center justify-between gap-3 text-sm"
      >
        <p class="text-ink-500">{{ rangeLabel }}</p>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 font-medium disabled:opacity-40 dark:border-ink-600"
            :disabled="page <= 1 || loading"
            @click="setPage(page - 1)"
          >
            Previous
          </button>
          <span class="min-w-[6.5rem] text-center text-ink-600 dark:text-ink-300">
            Page {{ page }} of {{ totalPages }}
          </span>
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 font-medium disabled:opacity-40 dark:border-ink-600"
            :disabled="page >= totalPages || loading"
            @click="setPage(page + 1)"
          >
            Next
          </button>
        </div>
      </div>
      <p v-else class="text-sm text-ink-500">{{ rangeLabel }}</p>
    </template>
  </section>
</template>
