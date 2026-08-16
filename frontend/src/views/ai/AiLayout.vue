<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
const tabs = [
  { to: '/ai/overview', label: 'Overview' },
  { to: '/ai/providers', label: 'Providers' },
  { to: '/ai/models', label: 'Models & Routing' },
  { to: '/ai/usage', label: 'Usage' },
]
</script>

<template>
  <section class="space-y-6">
    <div>
      <h1 class="font-display text-2xl font-bold sm:text-3xl">AI</h1>
      <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
        AI Control Center — how AI is behaving and how you control it.
      </p>
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
