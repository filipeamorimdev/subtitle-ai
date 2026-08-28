<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api } from '../services/api'
import type { MediaItem, VoiceCast, VoiceCastSuggestion } from '../types'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()

const media = ref<MediaItem | null>(null)
const draft = ref<VoiceCast | null>(null)
const cueTexts = ref<Record<number, string>>({})
const loading = ref(true)
const saving = ref(false)
const analysing = ref(false)
const requesting = ref(false)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

const mediaId = computed(() => Number(props.id))
const targetLanguage = computed(() => {
  const value = route.query.language
  return typeof value === 'string' && value.trim() ? value : 'pt-PT'
})

const mediaMeta = computed(() => {
  if (!media.value) return ''
  const parts = [media.value.media_type]
  if (media.value.season != null && media.value.episode != null) {
    parts.push(`S${String(media.value.season).padStart(2, '0')}E${String(media.value.episode).padStart(2, '0')}`)
  }
  if (media.value.episode_title) parts.push(media.value.episode_title)
  return parts.join(' · ')
})

function setCueTexts(suggestions: VoiceCastSuggestion[]) {
  cueTexts.value = Object.fromEntries(
    suggestions.map((suggestion, index) => [index, suggestion.cue_indices.join(', ')]),
  )
}

function editedSuggestions(): VoiceCastSuggestion[] {
  if (!draft.value) return []
  return draft.value.suggestions.map((suggestion, index) => {
    const cues = Array.from(
      new Set(
        (cueTexts.value[index] || '')
          .split(',')
          .map((value) => Number(value.trim()))
          .filter((value) => Number.isInteger(value) && value > 0),
      ),
    ).sort((left, right) => left - right)
    if (!cues.length) throw new Error(`Add at least one sampled cue for ${suggestion.speaker_id || `speaker ${index + 1}`}.`)
    return { ...suggestion, cue_indices: cues }
  })
}

async function load() {
  if (!Number.isFinite(mediaId.value)) {
    error.value = 'Invalid media id'
    loading.value = false
    return
  }
  loading.value = true
  error.value = null
  try {
    const [mediaRow, cast] = await Promise.all([
      api.getMedia(mediaId.value),
      api.getDubVoiceCast(mediaId.value, targetLanguage.value),
    ])
    media.value = mediaRow
    draft.value = cast
    setCueTexts(cast.suggestions)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveDraft() {
  if (!draft.value || saving.value) return false
  saving.value = true
  error.value = null
  notice.value = null
  try {
    const saved = await api.updateDubVoiceCast(mediaId.value, targetLanguage.value, {
      suggestions: editedSuggestions(),
      mix_mode: draft.value.mix_mode,
    })
    draft.value = saved
    setCueTexts(saved.suggestions)
    notice.value = 'Casting draft saved.'
    return true
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    return false
  } finally {
    saving.value = false
  }
}

async function analyseAgain() {
  if (analysing.value || requesting.value || !draft.value) return
  analysing.value = true
  error.value = null
  notice.value = null
  try {
    const result = await api.suggestDubVoiceCast(
      mediaId.value,
      targetLanguage.value,
      draft.value.mix_mode,
    )
    draft.value = result
    setCueTexts(result.suggestions)
    notice.value = 'A new analysis replaced the saved draft. Review it before requesting the dub.'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    analysing.value = false
  }
}

async function requestDub() {
  if (requesting.value || analysing.value || !(await saveDraft())) return
  requesting.value = true
  error.value = null
  try {
    await api.requestDubFromVoiceCast(mediaId.value, targetLanguage.value)
    await router.push(`/media/${mediaId.value}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    requesting.value = false
  }
}

watch([mediaId, targetLanguage], () => {
  load().catch(() => undefined)
})

onMounted(() => {
  load().catch(() => undefined)
})
</script>

<template>
  <section class="mx-auto max-w-5xl space-y-6 pb-10">
    <RouterLink class="text-sm text-accent hover:underline" :to="`/media/${mediaId}`">← Media</RouterLink>

    <p v-if="loading" class="text-sm text-ink-500">Loading saved casting draft…</p>
    <p v-else-if="error && !draft" class="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
      {{ error }}
      <RouterLink class="ml-1 font-semibold underline" :to="`/media/${mediaId}`">Start a new analysis from Request dub.</RouterLink>
    </p>

    <template v-else-if="draft && media">
      <header class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="text-sm font-medium text-accent">Saved voice casting draft</p>
          <h1 class="font-display text-3xl font-bold tracking-tight">{{ media.title }}</h1>
          <p class="mt-1 text-sm text-ink-500">{{ mediaMeta }} · {{ draft.target_language }}</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            :disabled="saving || analysing || requesting"
            @click="saveDraft"
          >
            {{ saving ? 'Saving…' : 'Save changes' }}
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            :disabled="saving || analysing || requesting"
            @click="requestDub"
          >
            {{ requesting ? 'Requesting…' : 'Request dub' }}
          </button>
        </div>
      </header>

      <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <main class="space-y-4">
          <section class="rounded-lg border border-accent/40 bg-accent/5 p-4 dark:border-accent/50">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 class="font-semibold">AI analysis</h2>
                <p class="mt-1 text-sm text-ink-600 dark:text-ink-300">
                  {{ draft.analysed_cue_count }} sampled dialogue cues · {{ draft.model_id }}
                </p>
              </div>
              <button
                type="button"
                class="rounded-md border border-accent px-3 py-1.5 text-sm font-semibold text-accent disabled:opacity-40"
                :disabled="saving || analysing || requesting"
                @click="analyseAgain"
              >
                {{ analysing ? 'Analysing source audio…' : 'Re-analyse' }}
              </button>
            </div>
            <p class="mt-3 text-xs text-ink-600 dark:text-ink-400">
              Re-analysis replaces this saved draft. It sends new short source-audio samples to the configured Audio Analysis model.
            </p>
          </section>

          <fieldset class="rounded-lg border border-ink-200 p-4 dark:border-ink-700">
            <legend class="px-1 text-sm font-semibold">Audio mix</legend>
            <label class="mt-2 flex cursor-pointer gap-2 text-sm">
              <input v-model="draft.mix_mode" type="radio" value="background_preserved" />
              <span><strong>Preserve background audio</strong><span class="block text-xs text-ink-500">Music, ambience, and effects are kept.</span></span>
            </label>
            <label class="mt-3 flex cursor-pointer gap-2 text-sm">
              <input v-model="draft.mix_mode" type="radio" value="voiceover_preview" />
              <span><strong>Voiceover preview</strong><span class="block text-xs text-ink-500">Generated dialogue only; avoids source separation.</span></span>
            </label>
          </fieldset>

          <section class="space-y-3">
            <div>
              <h2 class="font-display text-xl font-bold">Speaker assignments</h2>
              <p class="mt-1 text-sm text-ink-500">Edit, disable, or reassign each proposed speaker before the dub is queued.</p>
            </div>
            <article
              v-for="(suggestion, index) in draft.suggestions"
              :key="index"
              class="rounded-lg border border-ink-200 bg-white p-4 dark:border-ink-700 dark:bg-ink-900"
            >
              <div class="flex flex-wrap items-center justify-between gap-3">
                <label class="flex cursor-pointer items-center gap-2 text-sm font-semibold">
                  <input v-model="suggestion.enabled" type="checkbox" />
                  Use this assignment
                </label>
                <span v-if="suggestion.confidence != null" class="text-xs text-ink-500">
                  {{ Math.round(suggestion.confidence * 100) }}% analysis confidence
                </span>
              </div>
              <div class="mt-4 grid gap-3 sm:grid-cols-2">
                <label class="block text-xs font-medium text-ink-600 dark:text-ink-300">
                  Speaker label
                  <input v-model="suggestion.speaker_id" class="mt-1 w-full rounded border border-ink-300 bg-white px-2 py-1.5 text-sm dark:border-ink-600 dark:bg-ink-800" />
                </label>
                <label class="block text-xs font-medium text-ink-600 dark:text-ink-300">
                  Sampled cue IDs
                  <input v-model="cueTexts[index]" class="mt-1 w-full rounded border border-ink-300 bg-white px-2 py-1.5 font-mono text-sm dark:border-ink-600 dark:bg-ink-800" />
                </label>
              </div>
              <label class="mt-3 block text-xs font-medium text-ink-600 dark:text-ink-300">
                Voice style note from the analyser
                <textarea v-model="suggestion.voice_style" rows="2" class="mt-1 w-full rounded border border-ink-300 bg-white px-2 py-1.5 text-sm dark:border-ink-600 dark:bg-ink-800" />
              </label>
              <label class="mt-3 block text-xs font-medium text-ink-600 dark:text-ink-300">
                Piper speech voice
                <input
                  v-model="suggestion.voice_model"
                  list="piper-voice-models"
                  class="mt-1 w-full rounded border border-ink-300 bg-white px-2 py-1.5 font-mono text-sm dark:border-ink-600 dark:bg-ink-800"
                  :disabled="!suggestion.enabled"
                />
              </label>
            </article>
          </section>
        </main>

        <aside class="space-y-4">
          <section class="rounded-lg border border-ink-200 bg-ink-50 p-4 text-sm dark:border-ink-700 dark:bg-ink-800/50">
            <h2 class="font-semibold">Why the same Piper voice?</h2>
            <p class="mt-2 text-ink-600 dark:text-ink-300">
              {{ draft.model_id }} analyses and groups speakers. It does not synthesize their speech.
              The fields below use Piper, whose verified European Portuguese catalogue currently has one supported voice, so it is prefilled for every speaker.
            </p>
            <p class="mt-2 text-xs text-ink-500">
              You can type a different valid Piper model ID, but use a compatible Portuguese voice; it is downloaded when the dub starts.
            </p>
            <datalist id="piper-voice-models">
              <option v-for="model in draft.available_voice_models" :key="model.id" :value="model.id">{{ model.label }}</option>
            </datalist>
            <ul class="mt-3 space-y-1 text-xs text-ink-500">
              <li v-for="model in draft.available_voice_models" :key="model.id">
                {{ model.label }} — <code>{{ model.id }}</code>
              </li>
            </ul>
          </section>
          <p v-if="notice" class="rounded-md border border-green-300 bg-green-50 p-3 text-sm text-green-800 dark:border-green-900 dark:bg-green-950/30 dark:text-green-300">{{ notice }}</p>
          <p v-if="error" class="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">{{ error }}</p>
        </aside>
      </div>
    </template>
  </section>
</template>
