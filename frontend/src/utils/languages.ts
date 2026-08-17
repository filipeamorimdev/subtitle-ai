/** Regional-indicator flag emoji from an ISO 3166-1 alpha-2 code. */
export function flagEmoji(region: string | null | undefined): string {
  if (!region) return '🏳️'
  const letters = region.trim().toUpperCase()
  if (!/^[A-Z]{2}$/.test(letters)) return '🏳️'
  return String.fromCodePoint(
    ...[...letters].map((char) => 0x1f1e6 + char.charCodeAt(0) - 65),
  )
}

const DEFAULT_LANGUAGE_REGIONS: Record<string, string> = {
  ar: 'SA',
  de: 'DE',
  en: 'GB',
  es: 'ES',
  fa: 'IR',
  fr: 'FR',
  it: 'IT',
  ja: 'JP',
  ko: 'KR',
  nl: 'NL',
  pl: 'PL',
  pt: 'PT',
  ru: 'RU',
  sw: 'TZ',
  zh: 'CN',
}

export function flagFromLanguageCode(code: string | null | undefined): string {
  if (!code) return '🏳️'
  const parts = code.trim().split(/[-_]/)
  if (parts.length >= 2 && parts[1].length === 2) {
    return flagEmoji(parts[1])
  }
  const fallback = DEFAULT_LANGUAGE_REGIONS[parts[0].toLowerCase()]
  return fallback ? flagEmoji(fallback) : '🏳️'
}
