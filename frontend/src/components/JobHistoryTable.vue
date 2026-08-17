<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import type { JobAction } from '../types'
import { formatDateTime, formatDuration } from '../utils/datetime'
import { canRetryJob, jobStatusClass } from '../utils/status'

const props = withDefaults(
  defineProps<{
    actions: JobAction[]
    error?: string | null
    emptyMessage?: string
    linkCurrent?: boolean
    showRetry?: boolean
    retryingId?: number | null
    pageSize?: number
  }>(),
  {
    error: null,
    emptyMessage: 'No actions recorded yet.',
    linkCurrent: true,
    showRetry: true,
    retryingId: null,
    pageSize: 0,
  },
)

const emit = defineEmits<{
  retry: [id: number]
}>()

const page = ref(1)

const totalPages = computed(() => {
  if (!props.pageSize || props.actions.length === 0) return 1
  return Math.max(1, Math.ceil(props.actions.length / props.pageSize))
})

const pagedActions = computed(() => {
  if (!props.pageSize) return props.actions
  const start = (page.value - 1) * props.pageSize
  return props.actions.slice(start, start + props.pageSize)
})

const rangeLabel = computed(() => {
  if (!props.actions.length || !props.pageSize) return ''
  const start = (page.value - 1) * props.pageSize + 1
  const end = Math.min(page.value * props.pageSize, props.actions.length)
  return `${start}–${end} of ${props.actions.length}`
})

const showPager = computed(() => Boolean(props.pageSize) && totalPages.value > 1)

watch(
  () => [props.actions.length, props.pageSize] as const,
  () => {
    if (page.value > totalPages.value) page.value = totalPages.value
    if (page.value < 1) page.value = 1
  },
)

const iconBtnClass =
  'inline-flex shrink-0 items-center justify-center rounded-md p-1.5 text-ink-500 transition hover:bg-ink-100 hover:text-accent disabled:opacity-50 dark:hover:bg-ink-800'

function isJobItem(item: JobAction) {
  return item.kind !== 'task'
}

function shouldLink(item: JobAction) {
  if (!isJobItem(item)) return false
  return props.linkCurrent || !item.current
}

function actionKey(item: JobAction) {
  return `${item.kind || 'job'}-${item.id}`
}

function logsHref(item: JobAction) {
  return `/jobs/${item.id}?log=1`
}

function statsHref(item: JobAction) {
  return `/jobs/${item.id}/stats`
}
</script>

<template>
  <div>
    <p v-if="error" class="text-sm text-red-700 dark:text-red-300">{{ error }}</p>

    <!-- Mobile / tablet cards -->
    <div v-else class="space-y-3 lg:hidden">
      <p
        v-if="!actions.length"
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-8 text-sm text-ink-500 dark:border-ink-800 dark:bg-ink-900/60"
      >
        {{ emptyMessage }}
      </p>
      <article
        v-for="item in pagedActions"
        :key="`card-${actionKey(item)}`"
        class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
        :class="item.current ? 'bg-accent/5 dark:bg-accent/10' : ''"
      >
        <div class="flex items-start justify-between gap-3">
          <RouterLink
            v-if="shouldLink(item)"
            class="min-w-0 flex-1 capitalize font-medium text-accent hover:underline"
            :to="`/jobs/${item.id}`"
          >
            {{ item.action }}
            <span v-if="item.target_language" class="font-normal text-ink-500">
              {{ item.target_language }}
            </span>
            <span v-if="item.kind !== 'task'" class="text-ink-500">#{{ item.id }}</span>
          </RouterLink>
          <span v-else class="min-w-0 flex-1 capitalize font-medium">
            {{ item.action }}
            <span v-if="item.target_language" class="font-normal text-ink-500">
              {{ item.target_language }}
            </span>
            <span v-if="item.kind !== 'task'" class="text-ink-500">#{{ item.id }}</span>
          </span>
          <div class="flex shrink-0 items-center">
            <RouterLink
              v-if="isJobItem(item)"
              :class="iconBtnClass"
              :to="logsHref(item)"
              title="View logs"
              aria-label="View logs"
            >
              <svg
                class="h-4 w-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
                <path d="M16 13H8" />
                <path d="M16 17H8" />
                <path d="M10 9H8" />
              </svg>
            </RouterLink>
            <RouterLink
              v-if="isJobItem(item)"
              :class="iconBtnClass"
              :to="statsHref(item)"
              title="Usage stats"
              aria-label="Usage stats"
            >
              <svg
                class="h-4 w-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M18 20V10" />
                <path d="M12 20V4" />
                <path d="M6 20v-6" />
              </svg>
            </RouterLink>
            <button
              v-if="showRetry && isJobItem(item) && canRetryJob(item.status)"
              type="button"
              :class="iconBtnClass"
              title="Retry"
              aria-label="Retry"
              :disabled="retryingId === item.id"
              @click="emit('retry', item.id)"
            >
              <svg
                class="h-4 w-4"
                :class="retryingId === item.id ? 'animate-spin' : ''"
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
            </button>
          </div>
        </div>
        <dl class="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
          <div>
            <dt class="text-ink-500">Status</dt>
            <dd class="capitalize" :class="jobStatusClass(item.status)">{{ item.status }}</dd>
          </div>
          <div>
            <dt class="text-ink-500">Duration</dt>
            <dd>{{ formatDuration(item.duration_seconds) }}</dd>
          </div>
          <div class="col-span-2">
            <dt class="text-ink-500">When</dt>
            <dd>{{ formatDateTime(item.datetime) }}</dd>
          </div>
          <div v-if="item.message" class="col-span-2">
            <dt class="text-ink-500">Message</dt>
            <dd
              :class="
                item.status === 'failed'
                  ? 'text-red-700 dark:text-red-300'
                  : 'text-ink-600 dark:text-ink-300'
              "
            >
              {{ item.message }}
            </dd>
          </div>
        </dl>
      </article>
    </div>

    <!-- Desktop table -->
    <div v-if="!error" class="hidden overflow-x-auto lg:block">
      <table class="min-w-full text-left text-sm">
        <thead class="border-b border-ink-200 text-ink-500 dark:border-ink-800 dark:text-ink-300">
          <tr>
            <th class="py-2 pr-4 font-medium">Action</th>
            <th class="py-2 pr-4 font-medium">Date / time</th>
            <th class="py-2 pr-4 font-medium">Duration</th>
            <th class="py-2 pr-4 font-medium">Status</th>
            <th class="py-2 font-medium">Message</th>
            <th class="whitespace-nowrap py-2 pl-2 font-medium">
              <span class="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!actions.length">
            <td colspan="6" class="py-4 text-ink-500">{{ emptyMessage }}</td>
          </tr>
          <tr
            v-for="item in pagedActions"
            :key="actionKey(item)"
            class="border-b border-ink-100 last:border-0 dark:border-ink-800/80"
            :class="item.current ? 'bg-accent/5' : ''"
          >
            <td class="py-3 pr-4 align-top">
              <RouterLink
                v-if="shouldLink(item)"
                class="capitalize text-accent hover:underline"
                :to="`/jobs/${item.id}`"
              >
                {{ item.action }}
                <span v-if="item.target_language" class="font-normal text-ink-500">
                  {{ item.target_language }}
                </span>
                <span v-if="item.kind !== 'task'" class="text-ink-500">#{{ item.id }}</span>
              </RouterLink>
              <span v-else class="capitalize font-medium">
                {{ item.action }}
                <span v-if="item.target_language" class="font-normal text-ink-500">
                  {{ item.target_language }}
                </span>
                <span v-if="item.kind !== 'task'" class="text-ink-500">#{{ item.id }}</span>
              </span>
            </td>
            <td class="py-3 pr-4 align-top whitespace-nowrap text-ink-600 dark:text-ink-300">
              {{ formatDateTime(item.datetime) }}
            </td>
            <td class="py-3 pr-4 align-top whitespace-nowrap text-ink-600 dark:text-ink-300">
              {{ formatDuration(item.duration_seconds) }}
            </td>
            <td class="py-3 pr-4 align-top capitalize" :class="jobStatusClass(item.status)">
              {{ item.status }}
            </td>
            <td
              class="py-3 align-top break-words"
              :class="
                item.status === 'failed'
                  ? 'text-red-700 dark:text-red-300'
                  : 'text-ink-600 dark:text-ink-300'
              "
            >
              {{ item.message || '—' }}
            </td>
            <td class="whitespace-nowrap py-3 pl-2 align-top">
              <div class="flex items-center justify-end">
                <RouterLink
                  v-if="isJobItem(item)"
                  :class="iconBtnClass"
                  :to="logsHref(item)"
                  title="View logs"
                  aria-label="View logs"
                >
                  <svg
                    class="h-4 w-4"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                    <path d="M16 13H8" />
                    <path d="M16 17H8" />
                    <path d="M10 9H8" />
                  </svg>
                </RouterLink>
                <RouterLink
                  v-if="isJobItem(item)"
                  :class="iconBtnClass"
                  :to="statsHref(item)"
                  title="Usage stats"
                  aria-label="Usage stats"
                >
                  <svg
                    class="h-4 w-4"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M18 20V10" />
                    <path d="M12 20V4" />
                    <path d="M6 20v-6" />
                  </svg>
                </RouterLink>
                <button
                  v-if="showRetry && isJobItem(item) && canRetryJob(item.status)"
                  type="button"
                  :class="iconBtnClass"
                  title="Retry"
                  aria-label="Retry"
                  :disabled="retryingId === item.id"
                  @click="emit('retry', item.id)"
                >
                  <svg
                    class="h-4 w-4"
                    :class="retryingId === item.id ? 'animate-spin' : ''"
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
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div
      v-if="!error && showPager"
      class="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm"
    >
      <p class="text-ink-500">{{ rangeLabel }}</p>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="rounded-md border border-ink-300 px-3 py-1.5 font-medium disabled:opacity-40 dark:border-ink-600"
          title="Previous page"
          aria-label="Previous page"
          :disabled="page <= 1"
          @click="page -= 1"
        >
          Previous
        </button>
        <span class="min-w-[6.5rem] text-center text-ink-600 dark:text-ink-300">
          Page {{ page }} of {{ totalPages }}
        </span>
        <button
          type="button"
          class="rounded-md border border-ink-300 px-3 py-1.5 font-medium disabled:opacity-40 dark:border-ink-600"
          title="Next page"
          aria-label="Next page"
          :disabled="page >= totalPages"
          @click="page += 1"
        >
          Next
        </button>
      </div>
    </div>
  </div>
</template>
