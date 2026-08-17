<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../services/api'
import { useAppStore } from '../stores/app'
import type { GlossaryScope, GlossaryTerm } from '../types'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

const scopes = ref<GlossaryScope[]>([])
const suggested = ref<GlossaryTerm[]>([])
const terms = ref<GlossaryTerm[]>([])
const selectedScopeId = ref<number | null>(null)
const busy = ref(false)
const error = ref<string | null>(null)
const tab = ref<'scopes' | 'review'>('scopes')
const selectedReviewIds = ref<Set<number>>(new Set())

const newTerm = ref({
  source: '',
  target: '',
  term_type: 'character',
  policy: 'localize',
  locked: true,
})

const parentDraft = ref<number | null>(null)

const selectedScope = computed(() =>
  scopes.value.find((scope) => scope.id === selectedScopeId.value) || null,
)

const universeScopes = computed(() => scopes.value.filter((s) => s.kind === 'universe'))
const mediaScopes = computed(() => scopes.value.filter((s) => s.kind !== 'universe'))

const selectedReviewCount = computed(() => selectedReviewIds.value.size)

const allReviewSelected = computed(
  () => suggested.value.length > 0 && suggested.value.every((term) => selectedReviewIds.value.has(term.id)),
)

const termTypes = ['character', 'place', 'organization', 'title', 'catchphrase', 'other']
const policies = ['keep', 'localize', 'transliterate']

async function loadAll() {
  busy.value = true
  error.value = null
  try {
    const lang = store.settings?.target_language.code
    const [scopeRows, suggestedRows] = await Promise.all([
      api.getGlossaryScopes(lang ? { target_language: lang } : undefined),
      api.getSuggestedGlossaryTerms(lang),
    ])
    scopes.value = scopeRows
    suggested.value = suggestedRows
    pruneSelectedReviewIds()
    if (selectedScopeId.value == null && scopeRows.length) {
      const fromQuery = Number(route.query.scope)
      selectedScopeId.value = Number.isFinite(fromQuery) && fromQuery > 0
        ? fromQuery
        : scopeRows[0].id
    }
    if (route.query.tab === 'review') {
      tab.value = 'review'
    }
    await loadTerms()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function loadTerms() {
  if (!selectedScopeId.value) {
    terms.value = []
    parentDraft.value = null
    return
  }
  terms.value = await api.getGlossaryTerms(selectedScopeId.value)
  parentDraft.value = selectedScope.value?.parent_scope_id ?? null
}

async function selectScope(id: number) {
  selectedScopeId.value = id
  tab.value = 'scopes'
  await router.replace({ query: { ...route.query, scope: String(id), tab: undefined } })
  await loadTerms()
}

async function addTerm() {
  if (!selectedScopeId.value) return
  if (!newTerm.value.source.trim() || !newTerm.value.target.trim()) return
  busy.value = true
  error.value = null
  try {
    await api.createGlossaryTerm(selectedScopeId.value, {
      source: newTerm.value.source.trim(),
      target: newTerm.value.target.trim(),
      term_type: newTerm.value.term_type,
      policy: newTerm.value.policy,
      locked: newTerm.value.locked,
      status: 'active',
    })
    newTerm.value.source = ''
    newTerm.value.target = ''
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function saveTerm(term: GlossaryTerm) {
  busy.value = true
  error.value = null
  try {
    await api.updateGlossaryTerm(term.id, {
      source: term.source,
      target: term.target,
      term_type: term.term_type,
      policy: term.policy,
      locked: term.locked,
      status: term.status,
    })
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function removeTerm(term: GlossaryTerm) {
  if (!confirm(`Delete term "${term.source}"?`)) return
  busy.value = true
  try {
    await api.deleteGlossaryTerm(term.id)
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function review(term: GlossaryTerm, approve: boolean, lock = false) {
  busy.value = true
  error.value = null
  try {
    await api.reviewGlossaryTerm(term.id, approve, lock)
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

function pruneSelectedReviewIds() {
  const valid = new Set(suggested.value.map((term) => term.id))
  const next = new Set<number>()
  for (const id of selectedReviewIds.value) {
    if (valid.has(id)) next.add(id)
  }
  selectedReviewIds.value = next
}

function isReviewSelected(id: number) {
  return selectedReviewIds.value.has(id)
}

function toggleReviewSelected(id: number, checked: boolean) {
  const next = new Set(selectedReviewIds.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedReviewIds.value = next
}

function onReviewCheckboxChange(id: number, event: Event) {
  const target = event.target as HTMLInputElement
  toggleReviewSelected(id, target.checked)
}

function toggleAllReviewSelected(checked: boolean) {
  selectedReviewIds.value = checked
    ? new Set(suggested.value.map((term) => term.id))
    : new Set()
}

function onToggleAllReviewSelected(event: Event) {
  const target = event.target as HTMLInputElement
  toggleAllReviewSelected(target.checked)
}

async function reviewMultiple(approve: boolean) {
  const ids = [...selectedReviewIds.value]
  if (!ids.length) return
  busy.value = true
  error.value = null
  try {
    let failureCount = 0
    await Promise.all(
      ids.map(async (id) => {
        try {
          await api.reviewGlossaryTerm(id, approve)
        } catch {
          failureCount += 1
        }
      }),
    )
    selectedReviewIds.value = new Set()
    await loadAll()
    if (failureCount) {
      error.value = `Failed to ${approve ? 'approve' : 'reject'} ${failureCount} of ${ids.length} term(s).`
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

async function saveParent() {
  if (!selectedScopeId.value) return
  busy.value = true
  try {
    await api.updateGlossaryScope(selectedScopeId.value, {
      parent_scope_id: parentDraft.value,
      clear_parent: parentDraft.value == null,
    })
    await loadAll()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

function parentName(scope: GlossaryScope) {
  if (!scope.parent_scope_id) return '—'
  return scopes.value.find((s) => s.id === scope.parent_scope_id)?.display_name || `#${scope.parent_scope_id}`
}

function setTab(next: 'scopes' | 'review') {
  tab.value = next
  const query = { ...route.query }
  if (next === 'review') query.tab = 'review'
  else delete query.tab
  router.replace({ query })
}

onMounted(async () => {
  if (!store.settings) {
    await store.loadSettings().catch(() => undefined)
  }
  await loadAll()
})

watch(
  () => store.settings?.target_language.code,
  () => {
    loadAll().catch(() => undefined)
  },
)

watch(
  () => route.query.tab,
  (value) => {
    if (value === 'review') tab.value = 'review'
    else if (value === 'scopes' || value == null) tab.value = 'scopes'
  },
)
</script>

<template>
  <section class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div class="min-w-0">
        <h2 class="font-display text-xl font-bold sm:text-2xl">Glossary</h2>
        <p class="mt-1 text-sm text-ink-600 sm:text-base dark:text-ink-300">
          Persistent term memory for series, movies, and shared universes.
        </p>
      </div>
      <div class="flex gap-2">
        <button
          type="button"
          class="rounded-md px-3 py-2 text-sm font-medium"
          :class="tab === 'scopes' ? 'bg-accent text-white' : 'bg-ink-100 dark:bg-ink-800'"
          @click="setTab('scopes')"
        >
          Scopes
        </button>
        <button
          type="button"
          class="rounded-md px-3 py-2 text-sm font-medium"
          :class="tab === 'review' ? 'bg-accent text-white' : 'bg-ink-100 dark:bg-ink-800'"
          @click="setTab('review')"
        >
          Review
          <span v-if="suggested.length" class="ml-1 opacity-80">({{ suggested.length }})</span>
        </button>
      </div>
    </div>

    <div v-if="selectedReviewCount" class="flex flex-wrap gap-2">
      <button
        type="button"
        class="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        :disabled="busy"
        @click="reviewMultiple(true)"
      >
        Approve multiple ({{ selectedReviewCount }})
      </button>
      <button
        type="button"
        class="rounded-md bg-red-600/90 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        :disabled="busy"
        @click="reviewMultiple(false)"
      >
        Reject multiple ({{ selectedReviewCount }})
      </button>
    </div>

    <p v-if="error" class="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
      {{ error }}
    </p>

    <div v-if="tab === 'review'" class="min-w-0 space-y-3">
      <p
        v-if="!suggested.length"
        class="rounded-xl border border-ink-200 bg-white/80 px-4 py-8 text-sm text-ink-500 dark:border-ink-800 dark:bg-ink-900/60"
      >
        No suggested terms awaiting review.
      </p>

      <!-- Mobile / tablet review cards -->
      <div class="space-y-3 lg:hidden">
        <article
          v-for="term in suggested"
          :key="`review-card-${term.id}`"
          class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
        >
          <div class="flex items-start gap-3">
            <input
              class="mt-1"
              type="checkbox"
              :checked="isReviewSelected(term.id)"
              :aria-label="`Select ${term.source}`"
              @change="onReviewCheckboxChange(term.id, $event)"
            />
            <div class="min-w-0 flex-1">
              <button class="text-left text-sm text-accent hover:underline" type="button" @click="selectScope(term.scope_id)">
                {{ term.scope_name || term.scope_id }}
              </button>
              <div class="mt-2 font-medium">{{ term.source }} → {{ term.target }}</div>
              <div class="mt-1 text-xs text-ink-500">{{ term.term_type }} · {{ term.policy }}</div>
            </div>
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              class="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
              type="button"
              :disabled="busy"
              @click="review(term, true)"
            >
              Approve
            </button>
            <button
              class="rounded-md bg-ink-800 px-2 py-1 text-xs font-medium text-white disabled:opacity-50 dark:bg-ink-200 dark:text-ink-900"
              type="button"
              :disabled="busy"
              @click="review(term, true, true)"
            >
              Approve + lock
            </button>
            <button
              class="rounded-md bg-red-600/90 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
              type="button"
              :disabled="busy"
              @click="review(term, false)"
            >
              Reject
            </button>
          </div>
        </article>
      </div>

      <div class="hidden min-w-0 overflow-x-auto rounded-xl border border-ink-200 bg-white/80 lg:block dark:border-ink-800 dark:bg-ink-900/60">
        <table class="min-w-[48rem] w-full text-left text-sm">
          <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
            <tr>
              <th class="px-4 py-3 font-medium">Scope</th>
              <th class="px-4 py-3 font-medium">Source</th>
              <th class="px-4 py-3 font-medium">Target</th>
              <th class="px-4 py-3 font-medium">Type</th>
              <th class="px-4 py-3 font-medium">Policy</th>
              <th class="px-4 py-3 font-medium">
                <label class="inline-flex items-center gap-2">
                  <input
                    type="checkbox"
                    :checked="allReviewSelected"
                    :disabled="!suggested.length"
                    :indeterminate.prop="selectedReviewCount > 0 && !allReviewSelected"
                    aria-label="Select all suggested terms"
                    @change="onToggleAllReviewSelected($event)"
                  />
                  Actions
                </label>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!suggested.length">
              <td class="px-4 py-8 text-ink-500" colspan="6">No suggested terms awaiting review.</td>
            </tr>
            <tr
              v-for="term in suggested"
              :key="term.id"
              class="border-t border-ink-100 dark:border-ink-800"
            >
              <td class="px-4 py-3">
                <button class="text-accent hover:underline" type="button" @click="selectScope(term.scope_id)">
                  {{ term.scope_name || term.scope_id }}
                </button>
              </td>
              <td class="px-4 py-3 font-medium">{{ term.source }}</td>
              <td class="px-4 py-3">{{ term.target }}</td>
              <td class="px-4 py-3">{{ term.term_type }}</td>
              <td class="px-4 py-3">{{ term.policy }}</td>
              <td class="px-4 py-3">
                <div class="flex flex-wrap items-center gap-2">
                  <input
                    type="checkbox"
                    :checked="isReviewSelected(term.id)"
                    :aria-label="`Select ${term.source}`"
                    @change="onReviewCheckboxChange(term.id, $event)"
                  />
                  <button
                    class="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                    type="button"
                    :disabled="busy"
                    @click="review(term, true)"
                  >
                    Approve
                  </button>
                  <button
                    class="rounded-md bg-ink-800 px-2 py-1 text-xs font-medium text-white disabled:opacity-50 dark:bg-ink-200 dark:text-ink-900"
                    type="button"
                    :disabled="busy"
                    @click="review(term, true, true)"
                  >
                    Approve + lock
                  </button>
                  <button
                    class="rounded-md bg-red-600/90 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                    type="button"
                    :disabled="busy"
                    @click="review(term, false)"
                  >
                    Reject
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-else class="grid min-w-0 gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside class="min-w-0 space-y-4">
        <div class="rounded-xl border border-ink-200 bg-white/80 p-3 dark:border-ink-800 dark:bg-ink-900/60">
          <h2 class="text-xs font-semibold uppercase tracking-wide text-ink-500">Universes</h2>
          <ul class="mt-2 space-y-1">
            <li v-if="!universeScopes.length" class="px-2 py-2 text-sm text-ink-500">None yet</li>
            <li v-for="scope in universeScopes" :key="scope.id">
              <button
                type="button"
                class="flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm"
                :class="
                  selectedScopeId === scope.id
                    ? 'bg-accent/15 text-ink-900 dark:text-white'
                    : 'hover:bg-ink-100 dark:hover:bg-ink-800'
                "
                @click="selectScope(scope.id)"
              >
                <span>{{ scope.display_name }}</span>
                <span class="text-xs text-ink-500">{{ scope.term_count }}</span>
              </button>
            </li>
          </ul>
        </div>
        <div class="rounded-xl border border-ink-200 bg-white/80 p-3 dark:border-ink-800 dark:bg-ink-900/60">
          <h2 class="text-xs font-semibold uppercase tracking-wide text-ink-500">Series & movies</h2>
          <ul class="mt-2 max-h-[28rem] space-y-1 overflow-auto">
            <li v-if="!mediaScopes.length" class="px-2 py-2 text-sm text-ink-500">None yet</li>
            <li v-for="scope in mediaScopes" :key="scope.id">
              <button
                type="button"
                class="flex w-full flex-col rounded-md px-2 py-2 text-left text-sm"
                :class="
                  selectedScopeId === scope.id
                    ? 'bg-accent/15 text-ink-900 dark:text-white'
                    : 'hover:bg-ink-100 dark:hover:bg-ink-800'
                "
                @click="selectScope(scope.id)"
              >
                <span class="font-medium">{{ scope.display_name }}</span>
                <span class="text-xs text-ink-500">
                  {{ scope.kind }} · {{ scope.term_count }} terms
                  <template v-if="scope.suggested_count"> · {{ scope.suggested_count }} suggested</template>
                </span>
              </button>
            </li>
          </ul>
        </div>
      </aside>

      <div v-if="selectedScope" class="min-w-0 space-y-4">
        <div class="rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <h2 class="break-words font-display text-xl font-bold sm:text-2xl">{{ selectedScope.display_name }}</h2>
              <p class="mt-1 break-all text-sm text-ink-500">
                {{ selectedScope.kind }} · {{ selectedScope.target_language }} · key {{ selectedScope.key }}
              </p>
              <p class="mt-1 text-sm text-ink-500">Parent: {{ parentName(selectedScope) }}</p>
            </div>
            <div v-if="selectedScope.kind !== 'universe'" class="flex w-full flex-wrap items-end gap-2 sm:w-auto">
              <label class="min-w-0 flex-1 text-xs text-ink-500 sm:flex-none">
                Link universe
                <select
                  v-model="parentDraft"
                  class="mt-1 block w-full rounded-md border border-ink-200 bg-white px-2 py-1.5 text-sm dark:border-ink-700 dark:bg-ink-950"
                >
                  <option :value="null">None</option>
                  <option v-for="universe in universeScopes" :key="universe.id" :value="universe.id">
                    {{ universe.display_name }}
                  </option>
                </select>
              </label>
              <button
                type="button"
                class="rounded-md bg-ink-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-ink-100 dark:text-ink-900"
                :disabled="busy"
                @click="saveParent"
              >
                Save link
              </button>
            </div>
          </div>
        </div>

        <form
          class="grid grid-cols-1 gap-3 rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60 sm:grid-cols-2"
          @submit.prevent="addTerm"
        >
          <input
            v-model="newTerm.source"
            class="min-w-0 rounded-md border border-ink-200 bg-white px-3 py-2 text-sm dark:border-ink-700 dark:bg-ink-950"
            placeholder="Source term"
            required
          />
          <input
            v-model="newTerm.target"
            class="min-w-0 rounded-md border border-ink-200 bg-white px-3 py-2 text-sm dark:border-ink-700 dark:bg-ink-950"
            placeholder="Preferred target"
            required
          />
          <select
            v-model="newTerm.term_type"
            class="min-w-0 rounded-md border border-ink-200 bg-white px-2 py-2 text-sm dark:border-ink-700 dark:bg-ink-950"
          >
            <option v-for="type in termTypes" :key="type" :value="type">{{ type }}</option>
          </select>
          <select
            v-model="newTerm.policy"
            class="min-w-0 rounded-md border border-ink-200 bg-white px-2 py-2 text-sm dark:border-ink-700 dark:bg-ink-950"
          >
            <option v-for="policy in policies" :key="policy" :value="policy">{{ policy }}</option>
          </select>
          <div class="flex flex-wrap items-center gap-3 sm:col-span-2">
            <label class="flex items-center gap-2 text-sm">
              <input v-model="newTerm.locked" type="checkbox" />
              Lock
            </label>
            <button
              type="submit"
              class="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
              :disabled="busy"
            >
              Add term
            </button>
          </div>
        </form>

        <!-- Mobile / tablet term cards -->
        <div class="space-y-3 lg:hidden">
          <p
            v-if="!terms.length"
            class="rounded-xl border border-ink-200 bg-white/80 px-4 py-8 text-sm text-ink-500 dark:border-ink-800 dark:bg-ink-900/60"
          >
            No terms yet. They appear after a translation extracts glossary candidates.
          </p>
          <article
            v-for="term in terms"
            :key="`term-card-${term.id}`"
            class="space-y-3 rounded-xl border border-ink-200 bg-white/80 p-4 dark:border-ink-800 dark:bg-ink-900/60"
          >
            <label class="block text-xs text-ink-500">
              Source
              <input
                v-model="term.source"
                class="mt-1 w-full rounded-md border border-ink-200 bg-transparent px-2 py-1.5 text-sm dark:border-ink-700"
              />
            </label>
            <label class="block text-xs text-ink-500">
              Target
              <input
                v-model="term.target"
                class="mt-1 w-full rounded-md border border-ink-200 bg-transparent px-2 py-1.5 text-sm dark:border-ink-700"
              />
            </label>
            <div class="grid grid-cols-2 gap-3">
              <label class="block text-xs text-ink-500">
                Type
                <select
                  v-model="term.term_type"
                  class="mt-1 w-full rounded-md border border-ink-200 bg-transparent px-2 py-1.5 text-sm dark:border-ink-700"
                >
                  <option v-for="type in termTypes" :key="type" :value="type">{{ type }}</option>
                </select>
              </label>
              <label class="block text-xs text-ink-500">
                Policy
                <select
                  v-model="term.policy"
                  class="mt-1 w-full rounded-md border border-ink-200 bg-transparent px-2 py-1.5 text-sm dark:border-ink-700"
                >
                  <option v-for="policy in policies" :key="policy" :value="policy">{{ policy }}</option>
                </select>
              </label>
            </div>
            <div class="flex flex-wrap items-center gap-3">
              <label class="block min-w-[8rem] flex-1 text-xs text-ink-500">
                Status
                <select
                  v-model="term.status"
                  class="mt-1 w-full rounded-md border border-ink-200 bg-transparent px-2 py-1.5 text-sm dark:border-ink-700"
                >
                  <option value="active">active</option>
                  <option value="suggested">suggested</option>
                  <option value="rejected">rejected</option>
                </select>
              </label>
              <label class="mt-4 flex items-center gap-2 text-sm">
                <input v-model="term.locked" type="checkbox" />
                Lock
              </label>
            </div>
            <div class="flex gap-2">
              <button
                type="button"
                class="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                :disabled="busy"
                @click="saveTerm(term)"
              >
                Save
              </button>
              <button
                type="button"
                class="rounded-md bg-red-600/90 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                :disabled="busy"
                @click="removeTerm(term)"
              >
                Delete
              </button>
            </div>
          </article>
        </div>

        <div class="hidden min-w-0 overflow-x-auto rounded-xl border border-ink-200 bg-white/80 lg:block dark:border-ink-800 dark:bg-ink-900/60">
          <table class="min-w-[52rem] w-full text-left text-sm">
            <thead class="border-b border-ink-200 bg-ink-50/80 text-ink-500 dark:border-ink-800 dark:bg-ink-950/50 dark:text-ink-300">
              <tr>
                <th class="px-3 py-3 font-medium">Source</th>
                <th class="px-3 py-3 font-medium">Target</th>
                <th class="px-3 py-3 font-medium">Type</th>
                <th class="px-3 py-3 font-medium">Policy</th>
                <th class="px-3 py-3 font-medium">Status</th>
                <th class="px-3 py-3 font-medium">Lock</th>
                <th class="px-3 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!terms.length">
                <td class="px-3 py-8 text-ink-500" colspan="7">
                  No terms yet. They appear after a translation extracts glossary candidates.
                </td>
              </tr>
              <tr
                v-for="term in terms"
                :key="term.id"
                class="border-t border-ink-100 dark:border-ink-800"
              >
                <td class="px-3 py-2">
                  <input
                    v-model="term.source"
                    class="w-full rounded-md border border-ink-200 bg-transparent px-2 py-1 dark:border-ink-700"
                  />
                </td>
                <td class="px-3 py-2">
                  <input
                    v-model="term.target"
                    class="w-full rounded-md border border-ink-200 bg-transparent px-2 py-1 dark:border-ink-700"
                  />
                </td>
                <td class="px-3 py-2">
                  <select
                    v-model="term.term_type"
                    class="rounded-md border border-ink-200 bg-transparent px-2 py-1 dark:border-ink-700"
                  >
                    <option v-for="type in termTypes" :key="type" :value="type">{{ type }}</option>
                  </select>
                </td>
                <td class="px-3 py-2">
                  <select
                    v-model="term.policy"
                    class="rounded-md border border-ink-200 bg-transparent px-2 py-1 dark:border-ink-700"
                  >
                    <option v-for="policy in policies" :key="policy" :value="policy">{{ policy }}</option>
                  </select>
                </td>
                <td class="px-3 py-2">
                  <select
                    v-model="term.status"
                    class="rounded-md border border-ink-200 bg-transparent px-2 py-1 dark:border-ink-700"
                  >
                    <option value="active">active</option>
                    <option value="suggested">suggested</option>
                    <option value="rejected">rejected</option>
                  </select>
                </td>
                <td class="px-3 py-2">
                  <input v-model="term.locked" type="checkbox" />
                </td>
                <td class="px-3 py-2">
                  <div class="flex gap-2">
                    <button
                      type="button"
                      class="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                      :disabled="busy"
                      @click="saveTerm(term)"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      class="rounded-md bg-red-600/90 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
                      :disabled="busy"
                      @click="removeTerm(term)"
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-else class="rounded-xl border border-dashed border-ink-300 px-4 py-16 text-center text-ink-500 dark:border-ink-700">
        No glossary scopes yet. Run a translation to create series/movie memory automatically.
      </div>
    </div>
  </section>
</template>
