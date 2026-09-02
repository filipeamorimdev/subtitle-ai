import { describe, expect, it } from 'vitest'
import { approvedCharacters, canRequestCharacterDub, unresolvedCueRows } from './voiceLibrary'

describe('voiceLibrary gating', () => {
  it('blocks dub when cues are unresolved', () => {
    const result = canRequestCharacterDub(
      { dub_ready: false, unresolved_cue_count: 2 },
      [{ status: 'assigned', character_id: 1 }],
    )
    expect(result.ok).toBe(false)
    expect(result.reason).toContain('2 cues')
  })

  it('allows dub when library is ready and cast exists', () => {
    const result = canRequestCharacterDub(
      { dub_ready: true, unresolved_cue_count: 0 },
      [{ status: 'assigned', character_id: 1 }],
    )
    expect(result.ok).toBe(true)
  })

  it('filters unresolved cue rows and approved characters', () => {
    const rows = [
      { status: 'assigned', character_id: 1 },
      { status: 'uncertain', character_id: null },
    ]
    expect(unresolvedCueRows(rows)).toHaveLength(1)
    expect(approvedCharacters([{ approval_status: 'approved' }, { approval_status: 'draft' }])).toHaveLength(1)
  })
})
