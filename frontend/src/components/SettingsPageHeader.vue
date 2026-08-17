<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    saveLabel?: string
    saving?: boolean
    disabled?: boolean
    saveType?: 'submit' | 'button'
    form?: string
  }>(),
  {
    saveLabel: 'Save',
    saveType: 'submit',
  },
)

const emit = defineEmits<{
  save: []
}>()

function onSaveClick() {
  if (props.saveType === 'button') emit('save')
}
</script>

<template>
  <header
    class="sticky top-0 z-20 -mx-1 flex items-start justify-between gap-3 bg-ink-50/95 px-1 py-3 backdrop-blur-md dark:bg-ink-950/95"
  >
    <div class="min-w-0">
      <h2 class="font-display text-lg font-semibold">{{ title }}</h2>
      <p v-if="description" class="mt-1 text-sm text-ink-600 dark:text-ink-300">
        {{ description }}
      </p>
    </div>
    <div class="flex shrink-0 flex-wrap items-center justify-end gap-2 pt-0.5">
      <slot name="actions" />
      <button
        class="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white shadow-sm disabled:opacity-60"
        :type="saveType"
        :form="form"
        :disabled="disabled || saving"
        @click="onSaveClick"
      >
        {{ saving ? 'Saving…' : saveLabel }}
      </button>
    </div>
  </header>
</template>
