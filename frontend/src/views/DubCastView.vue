<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { api } from '../services/api'
import type { AuditionCandidate, MediaItem, VoiceCharacter, VoiceLibrary } from '../types'
import { canRequestCharacterDub } from '../utils/voiceLibrary'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()

const media = ref<MediaItem | null>(null)
const library = ref<VoiceLibrary | null>(null)
const loading = ref(true)
const analysing = ref(false)
const extracting = ref(false)
const requesting = ref(false)
const auditioningId = ref<number | null>(null)
const approvingId = ref<number | null>(null)
const auditionClips = ref<Record<number, AuditionCandidate[]>>({})
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

const dubGate = computed(() => {
  if (!library.value) return { ok: false, reason: 'Loading voice library…' }
  return canRequestCharacterDub(library.value, library.value.episode_cast)
})

async function load() {
  if (!Number.isFinite(mediaId.value)) {
    error.value = 'Invalid media id'
    loading.value = false
    return
  }
  loading.value = true
  error.value = null
  try {
    const [mediaRow, voiceLibrary] = await Promise.all([
      api.getMedia(mediaId.value),
      api.getVoiceLibrary(mediaId.value, targetLanguage.value),
    ])
    media.value = mediaRow
    library.value = voiceLibrary
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function analyseEpisode() {
  if (analysing.value) return
  analysing.value = true
  error.value = null
  notice.value = null
  try {
    library.value = await api.analyseVoiceLibrary(
      mediaId.value,
      targetLanguage.value,
      library.value?.mix_mode ?? 'background_preserved',
    )
    notice.value = 'Episode cues mapped to characters. Review unresolved cues and approve voices.'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    analysing.value = false
  }
}

async function extractReferences() {
  if (extracting.value) return
  extracting.value = true
  error.value = null
  notice.value = null
  try {
    const candidates = await api.buildVoiceReferenceCandidates(mediaId.value, targetLanguage.value)
    for (const candidate of candidates) {
      const character = library.value?.characters.find((item) => item.character_key === candidate.character_key)
      if (!character) continue
      await api.adoptVoiceReference(mediaId.value, character.id, {
        relative_path: candidate.relative_path,
        source_cue_indices: candidate.cue_indices,
      })
    }
    library.value = await api.getVoiceLibrary(mediaId.value, targetLanguage.value)
    notice.value = 'Reference candidates imported for each detected character.'
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    extracting.value = false
  }
}

async function runAudition(character: VoiceCharacter) {
  if (auditioningId.value != null) return
  auditioningId.value = character.id
  error.value = null
  try {
    const model = character.approved_voice_model ?? library.value?.available_voice_models[0]?.id
    const result = await api.auditionVoiceCharacter(
      mediaId.value,
      character.id,
      targetLanguage.value,
      model ?? undefined,
    )
    auditionClips.value = { ...auditionClips.value, [character.id]: result.candidates.slice(0, 5) }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    auditioningId.value = null
  }
}

async function approveCharacter(character: VoiceCharacter) {
  const reference = character.references.find((item) => item.is_canonical) ?? character.references[0]
  const model = character.approved_voice_model ?? library.value?.available_voice_models[0]?.id
  if (!reference || !model) {
    error.value = 'Add a reference clip and choose a delivery profile before approving.'
    return
  }
  approvingId.value = character.id
  error.value = null
  try {
    await api.approveVoiceCharacter(mediaId.value, character.id, {
      reference_id: reference.id,
      voice_model: model,
      cfg_weight: typeof character.synthesis_params.cfg_weight === 'number' ? character.synthesis_params.cfg_weight : 0.35,
      synthesis_seed: 0,
    })
    library.value = await api.getVoiceLibrary(mediaId.value, targetLanguage.value)
    notice.value = `${character.display_name} approved for pt-PT dubbing.`
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    approvingId.value = null
  }
}

async function assignCue(cueIndex: number, characterId: number | null) {
  if (!library.value) return
  error.value = null
  try {
    library.value = await api.updateVoiceCueAssignments(mediaId.value, targetLanguage.value, [
      { cue_index: cueIndex, character_id: characterId },
    ])
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function requestDub() {
  if (requesting.value || !dubGate.value.ok) return
  requesting.value = true
  error.value = null
  try {
    await api.requestDubFromVoiceLibrary(mediaId.value, {
      target_language: targetLanguage.value,
      replace_existing: false,
      mix_mode: library.value?.mix_mode ?? 'background_preserved',
    })
    await router.push(`/media/${mediaId.value}`)
  } catch (err) {
    const e = err as Error & { code?: string }
    if (e.code !== 'output_exists') {
      error.value = err instanceof Error ? err.message : String(err)
      return
    }
    if (!window.confirm('A dub file already exists. Replace it with a newly generated dub?')) return
    try {
      await api.requestDubFromVoiceLibrary(mediaId.value, {
        target_language: targetLanguage.value,
        replace_existing: true,
        mix_mode: library.value?.mix_mode ?? 'background_preserved',
      })
      await router.push(`/media/${mediaId.value}`)
    } catch (retryError) {
      error.value = retryError instanceof Error ? retryError.message : String(retryError)
    }
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

    <p v-if="loading" class="text-sm text-ink-500">Loading series voice library…</p>
    <p v-else-if="error && !library" class="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
      {{ error }}
    </p>

    <template v-else-if="library && media">
      <header class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="text-sm font-medium text-accent">Series voice workspace</p>
          <h1 class="font-display text-3xl font-bold tracking-tight">{{ media.title }}</h1>
          <p class="mt-1 text-sm text-ink-500">{{ mediaMeta }} · {{ library.target_language }}</p>
        </div>
        <div class="flex flex-wrap gap-2">
          <button
            type="button"
            class="rounded-md border border-ink-300 px-3 py-1.5 text-sm font-semibold dark:border-ink-600"
            :disabled="analysing || extracting || requesting"
            @click="analyseEpisode"
          >
            {{ analysing ? 'Analysing episode…' : 'Map episode cues' }}
          </button>
          <button
            type="button"
            class="rounded-md border border-accent px-3 py-1.5 text-sm font-semibold text-accent disabled:opacity-40"
            :disabled="analysing || extracting || requesting"
            @click="extractReferences"
          >
            {{ extracting ? 'Extracting references…' : 'Extract references' }}
          </button>
          <button
            type="button"
            class="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            :disabled="!dubGate.ok || requesting"
            :title="dubGate.reason"
            @click="requestDub"
          >
            {{ requesting ? 'Requesting…' : 'Request character dub' }}
          </button>
        </div>
      </header>

      <p
        v-if="!dubGate.ok"
        class="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
      >
        {{ dubGate.reason || library.dub_ready_reason }}
      </p>

      <section class="space-y-3">
        <h2 class="font-display text-xl font-bold">Character library</h2>
        <p class="text-sm text-ink-500">
          Approve a canonical pt-PT reference per recurring character. Audition clips use reference-conditioned Chatterbox.
        </p>
        <article
          v-for="character in library.characters"
          :key="character.id"
          class="rounded-lg border border-ink-200 bg-white p-4 dark:border-ink-700 dark:bg-ink-900"
        >
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 class="font-semibold">{{ character.display_name }}</h3>
              <p class="text-xs text-ink-500">
                {{ character.approval_status }}
                <span v-if="character.approved_voice_model"> · {{ character.approved_voice_model }}</span>
              </p>
            </div>
            <div class="flex flex-wrap gap-2">
              <button
                type="button"
                class="rounded border border-ink-300 px-2 py-1 text-xs font-semibold dark:border-ink-600"
                :disabled="auditioningId === character.id"
                @click="runAudition(character)"
              >
                {{ auditioningId === character.id ? 'Auditioning…' : 'Audition pt-PT' }}
              </button>
              <button
                type="button"
                class="rounded bg-accent px-2 py-1 text-xs font-semibold text-white disabled:opacity-40"
                :disabled="approvingId === character.id || !character.references.length"
                @click="approveCharacter(character)"
              >
                {{ approvingId === character.id ? 'Approving…' : 'Approve voice' }}
              </button>
            </div>
          </div>
          <ul v-if="character.references.length" class="mt-3 space-y-1 text-xs text-ink-600 dark:text-ink-300">
            <li v-for="reference in character.references" :key="reference.id">
              <audio
                class="mt-1 w-full"
                controls
                preload="none"
                :src="api.voiceLibraryAudioUrl(mediaId, reference.relative_path)"
              />
              {{ reference.variant }} · {{ reference.relative_path }}
            </li>
          </ul>
          <ul v-if="auditionClips[character.id]?.length" class="mt-3 space-y-2">
            <li v-for="clip in auditionClips[character.id]" :key="`${clip.line_id}-${clip.cfg_weight}-${clip.seed}`">
              <p class="text-xs font-medium">{{ clip.line_id }} · cfg {{ clip.cfg_weight }}</p>
              <audio
                class="w-full"
                controls
                preload="none"
                :src="api.voiceLibraryAuditionUrl(mediaId, clip.wav_path)"
              />
            </li>
          </ul>
        </article>
      </section>

      <section class="space-y-3">
        <h2 class="font-display text-xl font-bold">Episode cue assignments</h2>
        <p class="text-sm text-ink-500">
          {{ library.episode_cast.length }} cues · {{ library.unresolved_cue_count }} unresolved
        </p>
        <div class="max-h-96 overflow-y-auto rounded-lg border border-ink-200 dark:border-ink-700">
          <table class="w-full text-left text-sm">
            <thead class="sticky top-0 bg-ink-50 text-xs uppercase dark:bg-ink-800">
              <tr>
                <th class="px-3 py-2">Cue</th>
                <th class="px-3 py-2">Label</th>
                <th class="px-3 py-2">Status</th>
                <th class="px-3 py-2">Character</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in library.episode_cast"
                :key="row.cue_index"
                class="border-t border-ink-100 dark:border-ink-800"
                :class="row.status === 'uncertain' || row.status === 'unresolved' ? 'bg-amber-50/60 dark:bg-amber-950/20' : ''"
              >
                <td class="px-3 py-2 font-mono">{{ row.cue_index }}</td>
                <td class="px-3 py-2">{{ row.speaker_label || '—' }}</td>
                <td class="px-3 py-2">{{ row.status }}</td>
                <td class="px-3 py-2">
                  <select
                    class="w-full rounded border border-ink-300 bg-white px-2 py-1 text-xs dark:border-ink-600 dark:bg-ink-800"
                    :value="row.character_id ?? ''"
                    @change="assignCue(row.cue_index, ($event.target as HTMLSelectElement).value ? Number(($event.target as HTMLSelectElement).value) : null)"
                  >
                    <option value="">Unresolved</option>
                    <option v-for="character in library.characters" :key="character.id" :value="character.id">
                      {{ character.display_name }}
                    </option>
                  </select>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <p v-if="notice" class="rounded-md border border-green-300 bg-green-50 p-3 text-sm text-green-800 dark:border-green-900 dark:bg-green-950/30 dark:text-green-300">{{ notice }}</p>
      <p v-if="error" class="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">{{ error }}</p>
    </template>
  </section>
</template>
