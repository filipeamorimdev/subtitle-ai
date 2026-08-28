<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import LanguageSelect from './LanguageSelect.vue'
import { api } from '../services/api'
import type { LanguageCatalogItem, MediaItem, MediaRef, VoiceCast } from '../types'

const props = defineProps<{
  open: boolean
  /** Pre-selected media (e.g. media detail page). */
  initialMedia?: MediaRef | null
  initialLanguage?: string | null
}>()

const emit = defineEmits<{
  close: []
  created: []
}>()

const languages = ref<LanguageCatalogItem[]>([])
const languageChoice = ref('')
const mixMode = ref<'background_preserved' | 'voiceover_preview'>('background_preserved')
const speakerVoiceOverrides = ref('')
const voiceCast = ref<VoiceCast | null>(null)
const voiceCastEnabled = ref<Record<number, boolean>>({})
const voiceModels = ref<Record<number, string>>({})
const casting = ref(false)
const submitting = ref(false)
const submitError = ref<string | null>(null)

const selected = computed(() => props.initialMedia ?? null)

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    submitError.value = null
    mixMode.value = 'background_preserved'
    speakerVoiceOverrides.value = ''
    voiceCast.value = null
    voiceCastEnabled.value = {}
    voiceModels.value = {}
    if (!languages.value.length) {
      try {
        languages.value = await api.getLanguages()
      } catch {
        languages.value = []
      }
    }
    if (props.initialLanguage) {
      languageChoice.value = props.initialLanguage
    } else if (languages.value.some((l) => l.code === 'pt-PT')) {
      languageChoice.value = 'pt-PT'
    } else if (languages.value[0]) {
      languageChoice.value = languages.value[0].code
    }
  },
)

watch(languageChoice, () => {
  // Audio samples are evaluated against one localized subtitle language at a time.
  voiceCast.value = null
  voiceCastEnabled.value = {}
  voiceModels.value = {}
})

function mediaLabel(item: MediaRef) {
  if (item.media_type === 'episode') {
    const ep =
      item.season != null && item.episode != null
        ? `S${String(item.season).padStart(2, '0')}E${String(item.episode).padStart(2, '0')}`
        : ''
    return [item.title, ep].filter(Boolean).join(' · ')
  }
  return [item.title, item.year].filter(Boolean).join(' · ')
}

function parseSpeakerVoiceOverrides(raw: string): Record<string, string> {
  const voices: Record<string, string> = {}
  for (const line of raw.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const separator = trimmed.indexOf('=')
    if (separator < 1 || !trimmed.slice(separator + 1).trim()) {
      throw new Error('Use one speaker mapping per line: Speaker = piper-voice-model')
    }
    voices[trimmed.slice(0, separator).trim()] = trimmed.slice(separator + 1).trim()
  }
  return voices
}

async function ensureSelectedMedia(): Promise<MediaItem> {
  if (!selected.value) throw new Error('Select media before requesting a dub.')
  return api.ensureMedia({
    provider_id: selected.value.provider_id,
    external_id: selected.value.external_id,
    media_type: selected.value.media_type,
    title: selected.value.title,
    year: selected.value.year,
    path: selected.value.path,
    season: selected.value.season,
    episode: selected.value.episode,
    episode_title: selected.value.episode_title,
    bazarr_movie_id: selected.value.bazarr_movie_id,
    bazarr_series_id: selected.value.bazarr_series_id,
    bazarr_episode_id: selected.value.bazarr_episode_id,
    parent_external_id: selected.value.parent_external_id,
  })
}

function suggestedVoiceOverrides(): Record<string, string> {
  const overrides: Record<string, string> = {}
  if (!voiceCast.value) return overrides
  voiceCast.value.suggestions.forEach((suggestion, index) => {
    const voiceModel = voiceModels.value[index]?.trim()
    if (!voiceCastEnabled.value[index] || !voiceModel) return
    suggestion.cue_indices.forEach((cueIndex) => {
      overrides[`cue:${cueIndex}`] = voiceModel
    })
  })
  return overrides
}

async function autoCastVoices() {
  if (!selected.value || !languageChoice.value || casting.value || submitting.value) return
  casting.value = true
  submitError.value = null
  try {
    const media = await ensureSelectedMedia()
    const result = await api.suggestDubVoiceCast(media.id, languageChoice.value)
    voiceCast.value = result
    voiceCastEnabled.value = Object.fromEntries(result.suggestions.map((_suggestion, index) => [index, true]))
    voiceModels.value = Object.fromEntries(
      result.suggestions.map((suggestion, index) => [index, suggestion.voice_model]),
    )
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : String(err)
  } finally {
    casting.value = false
  }
}

async function submit() {
  if (!selected.value || !languageChoice.value || submitting.value) return
  submitting.value = true
  submitError.value = null
  try {
    const media = await ensureSelectedMedia()
    await api.dubMedia(media.id, {
      target_language: languageChoice.value,
      replace_existing: true,
      mix_mode: mixMode.value,
      speaker_voices: {
        ...suggestedVoiceOverrides(),
        ...parseSpeakerVoiceOverrides(speakerVoiceOverrides.value),
      },
    })
    emit('created')
    emit('close')
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/50 p-4 sm:items-center"
    role="dialog"
    aria-modal="true"
    aria-labelledby="request-dub-title"
    @click.self="emit('close')"
  >
    <div
      class="w-full max-w-lg rounded-xl border border-ink-200 bg-white p-5 shadow-xl dark:border-ink-700 dark:bg-ink-900"
    >
      <div class="flex items-start justify-between gap-3">
        <div>
          <h2 id="request-dub-title" class="font-display text-xl font-bold">Request dub</h2>
          <p class="mt-1 text-sm text-ink-500">
            Creates a Portuguese dub (.dub.mkv) beside the original. The standard mode preserves
            music, ambience, and effects; an existing dub for the selected language is replaced.
          </p>
        </div>
        <button
          type="button"
          class="rounded-md px-2 py-1 text-sm text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
          title="Close"
          aria-label="Close"
          @click="emit('close')"
        >
          Close
        </button>
      </div>

      <div class="mt-5 space-y-4">
        <div v-if="selected">
          <span class="block text-sm font-medium text-ink-700 dark:text-ink-200">Media</span>
          <div
            class="mt-1.5 rounded-md border border-ink-200 bg-ink-50 px-3 py-2 dark:border-ink-700 dark:bg-ink-800"
          >
            <p class="truncate font-medium">{{ mediaLabel(selected) }}</p>
            <p class="text-xs uppercase tracking-wide text-ink-500">{{ selected.media_type }}</p>
          </div>
        </div>

        <div>
          <span class="block text-sm font-medium text-ink-700 dark:text-ink-200">
            Target language
          </span>
          <LanguageSelect
            v-model="languageChoice"
            :languages="languages"
            placeholder="Select target language"
          />
          <p v-if="!languages.length" class="mt-1 text-xs text-ink-500">
            No recognized languages loaded.
          </p>
        </div>

        <fieldset>
          <legend class="block text-sm font-medium text-ink-700 dark:text-ink-200">Audio mix</legend>
          <label
            class="mt-1.5 flex cursor-pointer gap-2 rounded-md border border-ink-200 p-3 dark:border-ink-700"
          >
            <input v-model="mixMode" type="radio" value="background_preserved" />
            <span>
              <span class="block text-sm font-medium">Preserve background audio</span>
              <span class="block text-xs text-ink-500">
                Keeps music, ambience, and effects, and makes the Portuguese track the default.
              </span>
            </span>
          </label>
          <label
            class="mt-2 flex cursor-pointer gap-2 rounded-md border border-ink-200 p-3 dark:border-ink-700"
          >
            <input v-model="mixMode" type="radio" value="voiceover_preview" />
            <span>
              <span class="block text-sm font-medium">Voiceover preview</span>
              <span class="block text-xs text-ink-500">
                Uses only the generated dialogue timeline, without background separation.
              </span>
            </span>
          </label>
        </fieldset>

        <fieldset class="rounded-md border border-accent/40 bg-accent/5 p-3 dark:border-accent/50">
          <legend class="px-1 text-sm font-semibold text-ink-800 dark:text-ink-100">
            Auto-cast voices with AI
          </legend>
          <p class="mt-1 text-xs text-ink-600 dark:text-ink-400">
            Analyses short dialogue samples from the source audio with the enabled Audio Analysis
            model. It uses Bazarr title, episode, and external-ID metadata when available; it does
            not browse IMDb. Review every suggestion before starting the dub.
          </p>
          <button
            type="button"
            class="mt-3 rounded-md border border-accent px-3 py-1.5 text-sm font-semibold text-accent disabled:opacity-40"
            :disabled="!selected || !languageChoice || casting || submitting"
            @click="autoCastVoices"
          >
            {{ casting ? 'Analysing source audio…' : voiceCast ? 'Re-analyse voices with AI' : 'Analyse voices with AI' }}
          </button>

          <div v-if="voiceCast" class="mt-3 space-y-2">
            <p class="text-xs text-ink-500">
              {{ voiceCast.analysed_cue_count }} sampled cues · {{ voiceCast.model_id }}
            </p>
            <div
              v-for="(suggestion, index) in voiceCast.suggestions"
              :key="`${suggestion.speaker_id}-${suggestion.cue_indices.join('-')}`"
              class="rounded-md border border-ink-200 bg-white p-2.5 dark:border-ink-700 dark:bg-ink-900"
            >
              <label class="flex cursor-pointer items-start gap-2">
                <input v-model="voiceCastEnabled[index]" type="checkbox" class="mt-1" />
                <span class="min-w-0 flex-1">
                  <span class="flex flex-wrap items-center gap-x-2 text-sm font-medium">
                    {{ suggestion.speaker_id }}
                    <span v-if="suggestion.confidence != null" class="text-xs font-normal text-ink-500">
                      {{ Math.round(suggestion.confidence * 100) }}% confidence
                    </span>
                  </span>
                  <span class="block text-xs text-ink-500">{{ suggestion.voice_style }}</span>
                  <span class="block text-xs text-ink-500">
                    Applies to sampled cues {{ suggestion.cue_indices.join(', ') }}
                  </span>
                </span>
              </label>
              <label class="mt-2 block text-xs font-medium text-ink-600 dark:text-ink-300">
                Piper voice model
                <input
                  v-model="voiceModels[index]"
                  class="mt-1 w-full rounded border border-ink-300 bg-white px-2 py-1 font-mono text-xs dark:border-ink-600 dark:bg-ink-800"
                  :disabled="!voiceCastEnabled[index]"
                />
              </label>
            </div>
          </div>
        </fieldset>

        <details>
          <summary class="cursor-pointer text-sm font-medium text-ink-700 dark:text-ink-200">
            Advanced manual voice overrides
          </summary>
          <p class="mt-1 text-xs text-ink-500">
            Optional label-to-Piper mappings, for example when subtitle lines include “Ryder:”.
            These override any auto-cast assignment for the same label.
          </p>
          <textarea
            v-model="speakerVoiceOverrides"
            class="mt-2 min-h-20 w-full rounded-md border border-ink-300 bg-white p-2 font-mono text-xs dark:border-ink-600 dark:bg-ink-800"
            placeholder="Ryder = pt_PT-tugão-medium"
          />
        </details>

        <p v-if="submitError" class="text-sm text-red-600">{{ submitError }}</p>

        <div class="flex justify-end gap-2 pt-1">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-medium dark:border-ink-600"
            title="Cancel"
            aria-label="Cancel"
            @click="emit('close')"
          >
            Cancel
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            title="Request dub"
            aria-label="Request dub"
            :disabled="!selected || !languageChoice || submitting"
            @click="submit"
          >
            {{ submitting ? 'Requesting…' : 'Request dub' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
