/**
 * Formatting money that arrived as a string.
 *
 * The API sends `"1200000.000"`, never a number, because a JSON number is an IEEE
 * 754 double and a salary that round-trips through one is no longer the salary that
 * was stored. These functions format for display and never compute: the only
 * arithmetic on money in this product happens in SQL, over the frozen column.
 *
 * `Number()` appears below and is safe here precisely because the result is thrown
 * away after rendering - it never goes back to the server, and never feeds a total.
 */

/** `$14,379.87`. The figure the list and the dashboard compare on. */
export function formatUsd(amount: string | null | undefined): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(amount))
}

/**
 * `₹1,200,000` - what the person is actually paid.
 *
 * Decimal places come from the currency, not from a default: KWD has three and JPY
 * has none, so a fixed two would print ¥1,200,000.00 and lose a Kuwaiti dinar's last
 * digit. `minorUnits` is on the reference payload for exactly this.
 */
export function formatLocal(
  amount: string | null | undefined,
  currency: string | null | undefined,
  minorUnits?: number,
): string {
  if (!amount || !currency) return '—'
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      ...(minorUnits === undefined
        ? {}
        : { minimumFractionDigits: minorUnits, maximumFractionDigits: minorUnits }),
    }).format(Number(amount))
  } catch {
    // An unknown ISO code should not blank the cell - the number is still the
    // number, and the code beside it still says what it is.
    return `${new Intl.NumberFormat('en-US').format(Number(amount))} ${currency}`
  }
}

/** `01 Apr 2026`. Unambiguous across locales, unlike 04/01/2026. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(date)
}

/**
 * `$800K` - a chart axis tick, not a figure.
 *
 * An axis is a scale to judge bar heights against; the table under every chart on
 * the dashboard carries the exact number. Formatting ticks like money printed
 * `$800,000.00` eight times down the side of a 240px chart, which crowded the plot
 * area until the bars had nowhere to sit.
 *
 * Deliberately not `formatUsd`: the two have different jobs, and a single function
 * with a `compact` flag would make every caller decide which one it meant.
 *
 * The scaling is done here rather than with `Intl`'s `notation: 'compact'`, which
 * is not stable across ICU versions - the same call returned `$800K` on one Node
 * build and `$800.0K` on another, so the test passed locally and failed in CI. An
 * axis label is not worth a dependency on which ICU the runner shipped with.
 */
export function formatUsdAxis(value: number): string {
  if (!Number.isFinite(value)) return '—'

  const sign = value < 0 ? '-' : ''
  let scaled = Math.abs(value)
  let unit = ''

  for (const [threshold, suffix] of [
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ] as const) {
    if (scaled >= threshold) {
      scaled /= threshold
      unit = suffix
      break
    }
  }

  // One decimal, and only when it says something: `$2.4M` is worth the character,
  // `$800.0K` is not.
  const rounded = unit === '' ? Math.round(scaled) : Math.round(scaled * 10) / 10
  const digits = Number.isInteger(rounded) ? 0 : 1

  return `${sign}$${rounded.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}${unit}`
}
