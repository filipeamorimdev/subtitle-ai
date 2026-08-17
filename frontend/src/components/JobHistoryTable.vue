<script setup lang="ts">
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
  }>(),
  {
    error: null,
    emptyMessage: 'No actions recorded yet.',
    linkCurrent: true,
    showRetry: true,
    retryingId: null,
  },
)

const emit = defineEmits<{
  retry: [id: number]
}>()

function shouldLink(item: JobAction) {
  return props.linkCurrent || !item.current
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
        v-for="item in actions"
        :key="`card-${item.id}`"
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
            <span class="text-ink-500">#{{ item.id }}</span>
          </RouterLink>
          <span v-else class="min-w-0 flex-1 capitalize font-medium">
            {{ item.action }}
            <span v-if="item.target_language" class="font-normal text-ink-500">
              {{ item.target_language }}
            </span>
            <span class="text-ink-500">#{{ item.id }}</span>
          </span>
          <button
            v-if="showRetry && canRetryJob(item.status)"
            type="button"
            class="shrink-0 rounded-md p-1.5 text-ink-500 transition hover:bg-ink-100 hover:text-accent disabled:opacity-50 dark:hover:bg-ink-800"
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
            <th v-if="showRetry" class="w-12 py-2 font-medium">
              <span class="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!actions.length">
            <td :colspan="showRetry ? 6 : 5" class="py-4 text-ink-500">{{ emptyMessage }}</td>
          </tr>
          <tr
            v-for="item in actions"
            :key="item.id"
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
                <span class="text-ink-500">#{{ item.id }}</span>
              </RouterLink>
              <span v-else class="capitalize font-medium">
                {{ item.action }}
                <span v-if="item.target_language" class="font-normal text-ink-500">
                  {{ item.target_language }}
                </span>
                <span class="text-ink-500">#{{ item.id }}</span>
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
            <td v-if="showRetry" class="py-3 pl-2 align-top">
              <button
                v-if="canRetryJob(item.status)"
                type="button"
                class="rounded-md p-1.5 text-ink-500 transition hover:bg-ink-100 hover:text-accent disabled:opacity-50 dark:hover:bg-ink-800"
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
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
