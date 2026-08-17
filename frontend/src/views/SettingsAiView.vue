<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
const tabs = [
  { to: '/settings/models/providers', label: 'Providers' },
  { to: '/settings/models/routing', label: 'Models & Routing' },
]
</script>

<template>
  <div class="space-y-6">
    <nav class="flex flex-wrap gap-1">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.to"
        :to="tab.to"
        class="rounded-md px-3 py-1.5 text-sm font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
        :class="route.path === tab.to ? 'bg-ink-100 font-semibold text-ink-900 dark:bg-ink-800 dark:text-white' : ''"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>
    <RouterView />
  </div>
</template>
