<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'

const route = useRoute()
const tabs = [
  { to: '/settings/general', label: 'General', match: '/settings/general' },
  { to: '/settings/providers', label: 'Providers', match: '/settings/providers' },
  { to: '/settings/models', label: 'Models', match: '/settings/models' },
  { to: '/settings/language', label: 'Language', match: '/settings/language' },
  { to: '/settings/glossary', label: 'Glossary', match: '/settings/glossary' },
]

function isActive(match: string) {
  if (match === '/settings/models') {
    return route.path === '/settings/models'
  }
  return route.path === match || route.path.startsWith(`${match}/`)
}
</script>

<template>
  <section class="space-y-6">
    <div>
      <h1 class="font-display text-2xl font-bold sm:text-3xl">Settings</h1>
    </div>
    <div class="flex flex-col gap-6 md:flex-row md:items-start">
      <aside class="shrink-0 md:w-56">
        <nav class="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
          <RouterLink
            v-for="tab in tabs"
            :key="tab.to"
            :to="tab.to"
            class="whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-200 dark:hover:bg-ink-800"
            :class="isActive(tab.match) ? 'bg-ink-100 text-ink-900 dark:bg-ink-800 dark:text-white' : ''"
          >
            {{ tab.label }}
          </RouterLink>
        </nav>
      </aside>
      <div class="min-w-0 flex-1">
        <RouterView />
      </div>
    </div>
  </section>
</template>
