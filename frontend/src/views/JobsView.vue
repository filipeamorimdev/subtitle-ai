<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useAppStore } from '../stores/app'

const store = useAppStore()
let timer: number | undefined

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
      <div
        v-for="item in [
          ['Pending', store.stats.pending],
          ['Processing', store.stats.processing],
          ['Completed', store.stats.completed],
          ['Failed', store.stats.failed],
          ['Skipped', store.stats.skipped],
          ['Cancelled', store.stats.cancelled],
        ]"
        :key="item[0]"
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-3 dark:border-ink-800 dark:bg-ink-900/60"
      >
        <div class="text-xs uppercase tracking-wide text-ink-500">{{ item[0] }}</div>
        <div class="mt-1 font-display text-2xl font-bold">{{ item[1] }}</div>
      </div>
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
          <tr v-if="!store.jobs.length">
            <td class="px-4 py-8 text-ink-500" colspan="6">No jobs yet.</td>
          </tr>
          <tr
            v-for="job in store.jobs"
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
