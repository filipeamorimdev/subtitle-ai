<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { OpenRouterModel } from '../types'

const props = defineProps<{
  modelValue: string
  models: OpenRouterModel[]
  loading?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  refresh: []
}>()

const open = ref(false)
const query = ref('')
const rootEl = ref<HTMLElement | null>(null)
const searchEl = ref<HTMLInputElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const activeIndex = ref(0)

def formatPrice(value: number | null | undefined): string {
  if (value == null) return 'Unknown'
  if (value <= 0) return 'Free'
  if (value < 0.01) return `$${value.toFixed(4)}`
  if (value < 1) return `$${value.toFixed(3)}`
  return `$${value.toFixed(2)}`
}

function formatPricing(model: OpenRouterModel): string {
  const prompt = formatPrice(model.prompt_price_per_million)
  const completion = formatPrice(model.completion_price_per_million)
  if (prompt === 'Free' && completion === 'Free') return 'Free'
  return `${prompt}/M in · ${completion}/M out`
}

const selectedModel = computed(() => props.models.find((m) => m.id === props.modelValue) || null)

const filteredModels = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.models
  return props.models.filter(
    (m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
  )
})

watch(filteredModels, () => {
  activeIndex.value = 0
})

watch(open, async (isOpen) => {
  if (!isOpen) return
  query.value = ''
  activeIndex.value = Math.max(
    0,
    filteredModels.value.findIndex((m) => m.id === props.modelValue),
  )
  await nextTick()
  searchEl.value?.focus()
  scrollActiveIntoView()
})

function selectModel(model: OpenRouterModel) {
  emit('update:modelValue', model.id)
  open.value = false
}

function toggle() {
  open.value = !open.value
}

function onDocumentClick(event: MouseEvent) {
  if (!open.value || !rootEl.value) return
  if (!rootEl.value.contains(event.target as Node)) {
    open.value = false
  }
}

function scrollActiveIntoView() {
  const list = listEl.value
  if (!list) return
  const item = list.querySelector<HTMLElement>(`[data-index="${activeIndex.value}"]`)
  item?.scrollIntoView({ block: 'nearest' })
}

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (!filteredModels.value.length) return
    activeIndex.value = (activeIndex.value + 1) % filteredModels.value.length
    scrollActiveIntoView()
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (!filteredModels.value.length) return
    activeIndex.value =
      (activeIndex.value - 1 + filteredModels.value.length) % filteredModels.value.length
    scrollActiveIntoView()
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const model = filteredModels.value[activeIndex.value]
    if (model) selectModel(model)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    open.value = false
  }
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentClick)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', onDocumentClick)
})
</script>

<template>
  <div ref="rootEl" class="relative min-w-0">
    <button
      type="button"
      class="mt-1 flex w-full min-w-0 items-center justify-between gap-3 rounded-md border border-ink-300 bg-transparent px-3 py-2 text-left dark:border-ink-600"
      :aria-expanded="open"
      aria-haspopup="listbox"
      @click="toggle"
    >
      <span class="min-w-0 flex-1">
        <span class="block truncate font-medium">
          {{ selectedModel?.name || modelValue || 'Select a model' }}
        </span>
        <span class="mt-0.5 block truncate text-xs text-ink-500">
          <template v-if="selectedModel">
            {{ selectedModel.id }} · {{ formatPricing(selectedModel) }}
          </template>
          <template v-else-if="modelValue">{{ modelValue }}</template>
          <template v-else>Sorted by price (cheapest first)</template>
        </span>
      </span>
      <span class="shrink-0 text-ink-500" aria-hidden="true">▾</span>
    </button>

    <div
      v-if="open"
      class="absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-ink-300 bg-white shadow-lg dark:border-ink-600 dark:bg-ink-900"
    >
      <div class="border-b border-ink-200 p-2 dark:border-ink-700">
        <input
          ref="searchEl"
          v-model="query"
          type="search"
          class="w-full rounded-md border border-ink-300 bg-transparent px-3 py-2 text-sm dark:border-ink-600"
          placeholder="Search models…"
          @keydown="onSearchKeydown"
        />
      </div>

      <div v-if="loading" class="px-3 py-4 text-sm text-ink-500">Loading models…</div>
      <div v-else-if="error" class="space-y-2 px-3 py-4 text-sm">
        <p class="text-red-700 dark:text-red-300">{{ error }}</p>
        <button
          type="button"
          class="rounded-md border border-ink-300 px-2 py-1 text-xs font-semibold dark:border-ink-600"
          @click="emit('refresh')"
        >
          Retry
        </button>
      </div>
      <div v-else-if="!filteredModels.length" class="px-3 py-4 text-sm text-ink-500">
        No models match “{{ query }}”.
      </div>
      <ul
        v-else
        ref="listEl"
        class="max-h-72 overflow-y-auto py-1"
        role="listbox"
      >
        <li
          v-for="(model, index) in filteredModels"
          :key="model.id"
          :data-index="index"
          role="option"
          :aria-selected="model.id === modelValue"
          class="cursor-pointer px-3 py-2 hover:bg-ink-100 dark:hover:bg-ink-800"
          :class="{
            'bg-ink-100 dark:bg-ink-800': index === activeIndex,
            'ring-1 ring-inset ring-accent/40': model.id === modelValue,
          }"
          @mouseenter="activeIndex = index"
          @click="selectModel(model)"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="truncate text-sm font-medium">{{ model.name }}</div>
              <div class="truncate text-xs text-ink-500">{{ model.id }}</div>
            </div>
            <div class="shrink-0 text-right text-xs text-ink-600 dark:text-ink-300">
              {{ formatPricing(model) }}
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>
