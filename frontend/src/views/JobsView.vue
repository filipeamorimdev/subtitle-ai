<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useAppStore } from '../stores/app'

const store = useAppStore()
let timer: number | undefined

type JobStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'skipped' | 'cancelled'

const statusFilter = ref<JobStatus | null>(null)

const statusCards = computed(() => [
  { label: 'Pending', status: 'pending' as const, count: store.stats?.pending ?? 0 },
  { label: 'Processing', status: 'processing' as const, count: store.stats?.processing ?? 0 },
  { label: 'Completed', status: 'completed' as const, count: store.stats?.completed ?? 0 },
  { label: 'Failed', status: 'failed' as const, count: store.stats?.failed ?? 0 },
  { label: 'Skipped', status: 'skipped' as const, count: store.stats?.skipped ?? 0 },
  { label: 'Cancelled', status: 'cancelled' as const, count: store.stats?.cancelled ?? 0 },
])

const filteredJobs = computed(() => {
  if (!statusFilter.value) return store.jobs
  return store.jobs.filter((job) => job.status === statusFilter.value)
})

function toggleStatusFilter(status: JobStatus) {
  statusFilter.value = statusFilter.value === status ? null : status
}

onMounted(async () => {
  await store.loadJobs()
  timer = window.setInterval(() => {
    store.loadJobs().catch(() => undefined)
  }, 3000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <section class="space-y-6">
    <div>
      <h1 class="font-display text-3xl font-bold">Jobs</h1>
      <p class="mt-1 text-ink-600 dark:text-ink-300">History of translation and extraction jobs.</p>
    </div>

    <div v-if="store.stats" class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <button
        v-for="item in statusCards"
        :key="item.status"
        type="button"
        class="rounded-xl border px-4 py-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        :class="
          statusFilter === item.status
            ? 'border-accent bg-accent/10 dark:bg-accent/20'
            : 'border-ink-200 bg-white/80 hover:border-accent/50 dark:border-ink-800 dark:bg-ink-900/60 dark:hover:border-accent/50'
        "
        :aria-pressed="statusFilter === item.status"
        @click="toggleStatusFilter(item.status)"
      >
        <div class="text-xs uppercase tracking-wide text-ink-500">{{ item.label }}</div>
        <div class="mt-1 font-display text-2xl font-bold">{{ item.count }}</div>
      </button>
    </div>

    <div class="overflow-hidden rounded-xl border border-ink-200 bg-white/80 dark:border-ink-800 dark:bg-ink-900/60">
      <table class="min-w-full text-left text-sm">
        <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
          <tr>
            <th class="px-4 py-3 font-medium">Media</th>
            <th class="px-4 py-3 font-medium">Kind</th>
            <th class="px-4 py-3 font-medium">Target</th>
            <th class="px-4 py-3 font-medium">Model</th>
            <th class="px-4 py-3 font-medium">Status</th>
            <th class="px-4 py-3 font-medium">Created</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filteredJobs.length">
            <td class="px-4 py-8 text-ink-500" colspan="6">
              {{ statusFilter ? `No ${statusFilter} jobs.` : 'No jobs yet.' }}
            </td>
          </tr>
          <tr
            v-for="job in filteredJobs"
            :key="job.id"
            class="border-t border-ink-100 dark:border-ink-800"
          >
            <td class="px-4 py-3">
              <RouterLink class="font-medium text-accent hover:underline" :to="`/jobs/${job.id}`">
                {{ job.media_title || job.media_path }}
              </RouterLink>
            </td>
            <td class="px-4 py-3 capitalize">{{ job.job_kind || 'translate' }}</td>
            <td class="px-4 py-3">{{ job.target_language }}</td>
            <td class="px-4 py-3">{{ job.model }}</td>
            <td class="px-4 py-3 capitalize">
              {{ job.status }}
              <span v-if="job.status === 'processing'" class="text-ink-500">
                ({{ job.progress }}%)
              </span>
            </td>
            <td class="px-4 py-3 text-ink-500">{{ job.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
