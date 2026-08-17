<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../services/api'
import { mediaHref } from '../utils/mediaNav'

const props = defineProps<{ id: string }>()
const router = useRouter()

onMounted(async () => {
  const taskId = Number(props.id)
  if (!Number.isFinite(taskId)) {
    await router.replace('/media')
    return
  }
  try {
    const task = await api.getLocalizationTask(taskId)
    await router.replace(mediaHref(task.media_item_id))
  } catch {
    await router.replace('/media')
  }
})
</script>

<template>
  <p class="text-sm text-ink-500">Opening media…</p>
</template>
