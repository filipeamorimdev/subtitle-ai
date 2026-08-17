<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
const tabs = [
  { to: '/ai/overview', label: 'Overview' },
  { to: '/ai/usage', label: 'Usage' },
]
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="font-display text-2xl font-bold sm:text-3xl">AI dashboard</h1>
        <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
          How AI is behaving — cost, quality, and routing.
        </p>
      </div>
      <RouterLink
        class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold text-ink-800 hover:bg-ink-100 dark:border-ink-600 dark:text-ink-100 dark:hover:bg-ink-800"
        to="/settings/models"
      >
        Configure models
      </RouterLink>
    </div>
    <nav class="flex flex-wrap gap-1 border-b border-ink-200 dark:border-ink-800">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.to"
        :to="tab.to"
        class="rounded-t-md px-3 py-2 text-sm font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
        :class="route.path === tab.to ? 'bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white' : ''"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>
    <RouterView />
  </section>
</template>
