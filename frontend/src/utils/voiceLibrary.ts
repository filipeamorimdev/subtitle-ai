/** Helpers for the series voice-library dub gate. */

export interface CueCastLike {
  status: string
  character_id: number | null
}

export interface CharacterLike {
  approval_status: string
}

export function canRequestCharacterDub(
  library: { dub_ready: boolean; unresolved_cue_count: number },
  episodeCast: CueCastLike[],
): { ok: boolean; reason: string } {
  if (!episodeCast.length) {
    return { ok: false, reason: 'Run episode analysis before requesting a character dub.' }
  }
  if (library.unresolved_cue_count > 0) {
    return {
      ok: false,
      reason: `${library.unresolved_cue_count} cues still need a character assignment.`,
    }
  }
  if (!library.dub_ready) {
    return { ok: false, reason: 'Approve a canonical voice reference for every assigned character.' }
  }
  return { ok: true, reason: '' }
}

export function unresolvedCueRows<T extends CueCastLike>(rows: T[]): T[] {
  return rows.filter((row) => row.status === 'uncertain' || row.status === 'unresolved')
}

export function approvedCharacters<T extends CharacterLike>(characters: T[]): T[] {
  return characters.filter((character) => character.approval_status === 'approved')
}
