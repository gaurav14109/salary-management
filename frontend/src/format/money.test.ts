import { describe, expect, it } from 'vitest'

import { formatDate, formatLocal, formatUsd, formatUsdAxis } from './money'

describe('formatUsd', () => {
  it('formats the string the API sent', () => {
    expect(formatUsd('14379.87')).toBe('$14,379.87')
  })

  it('shows a dash rather than $0.00 when there is no salary', () => {
    // $0.00 is a salary. "No salary recorded" is not.
    expect(formatUsd(null)).toBe('—')
    expect(formatUsd('')).toBe('—')
  })
})

describe('formatLocal', () => {
  it('uses the currency the person is paid in', () => {
    expect(formatLocal('1200000.000', 'INR', 2)).toContain('1,200,000')
  })

  it('respects a three-decimal currency', () => {
    // KWD has three minor units. Two would silently drop a digit of someone's pay.
    expect(formatLocal('12345.678', 'KWD', 3)).toContain('12,345.678')
  })

  it('respects a zero-decimal currency', () => {
    expect(formatLocal('1200000', 'JPY', 0)).not.toContain('.00')
  })

  it('falls back to the raw code rather than blanking the cell', () => {
    expect(formatLocal('1000', 'ZZZ')).toContain('ZZZ')
  })
})

describe('formatDate', () => {
  it('formats unambiguously', () => {
    // 04/01/2026 is the 4th of January to half the world.
    expect(formatDate('2026-04-01')).toBe('01 Apr 2026')
  })

  it('leaves an unparseable value alone instead of showing Invalid Date', () => {
    expect(formatDate('not-a-date')).toBe('not-a-date')
  })
})

describe('formatUsdAxis', () => {
  /* Axis ticks are not figures to read, they are a scale to judge against. The
     table underneath carries the exact number; repeating it on the axis with cents
     produced "$800,000.00" eight times down the side of a 240px chart, which is
     what was crowding the bars. */

  it('drops to thousands so a tick is four characters, not eleven', () => {
    expect(formatUsdAxis(800000)).toBe('$800K')
  })

  it('uses millions once the numbers get there', () => {
    expect(formatUsdAxis(2400000)).toBe('$2.4M')
  })

  it('keeps small numbers whole rather than rendering $0.0K', () => {
    expect(formatUsdAxis(850)).toBe('$850')
  })

  it('never shows cents - an axis is a scale, not a figure', () => {
    expect(formatUsdAxis(38431.75)).not.toContain('.75')
  })

  it('shows zero as a plain zero, since that is where the axis starts', () => {
    expect(formatUsdAxis(0)).toBe('$0')
  })
})

describe('formatUsdAxis is the same everywhere', () => {
  /* The first version used Intl's compact notation, which is not stable across ICU
     versions: the same call produced '\$800K' on one Node build and '\$800.0K' on
     another, so this suite passed locally and failed in CI. These cases pin the
     output rather than trusting whichever ICU the runner shipped with. */

  it.each([
    [0, '$0'],
    [850, '$850'],
    [999, '$999'],
    [1000, '$1K'],
    [38431.75, '$38.4K'],
    [800000, '$800K'],
    [2400000, '$2.4M'],
    [1_000_000_000, '$1B'],
    [-45000, '-$45K'],
  ])('formats %p as %p', (input, expected) => {
    expect(formatUsdAxis(input)).toBe(expected)
  })

  it('says nothing rather than NaN when handed one', () => {
    expect(formatUsdAxis(Number.NaN)).toBe('—')
  })
})
