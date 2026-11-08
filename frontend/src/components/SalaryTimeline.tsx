/**
 * Salary history as a timeline of decisions.
 *
 * A table of date ranges answers "what were they paid in Q2". A timeline answers
 * "why does this person earn what they earn", which is the question someone actually
 * opens this page with - so each entry carries the reason, who decided it, and when
 * the decision was made.
 *
 * `created_at` and `effective_from` are both shown and are not the same thing: a
 * raise entered in November and backdated to April is one event in November that
 * applies from April.
 */

import { Badge, Text } from '@mantine/core'

import type { Salary } from '../api/types'
import { formatDate, formatLocal, formatUsd } from '../format/money'
import styles from './SalaryTimeline.module.css'

const REASON_LABELS: Record<string, string> = {
  NEW_HIRE: 'New hire',
  PROMOTION: 'Promotion',
  MERIT: 'Merit',
  MARKET_ADJUSTMENT: 'Market adjustment',
  CORRECTION: 'Correction',
  IMPORT: 'Import',
}

export function SalaryTimeline({
  history,
  minorUnits,
}: {
  history: Salary[]
  minorUnits: Map<string, number>
}) {
  if (history.length === 0) {
    return (
      <div className="u-empty">
        <Text fw={500}>No salary recorded yet</Text>
        <Text size="sm">Nothing has been entered for this person.</Text>
      </div>
    )
  }

  return (
    <ol className={styles.timeline}>
      {history.map((row) => {
        const current = row.effective_to === null
        return (
          <li key={row.id} className={styles.entry}>
            <span
              className={`${styles.marker} ${current ? styles.markerCurrent : ''}`}
              aria-hidden="true"
            />
            <div className={styles.body}>
              <div className={styles.headline}>
                <span className="u-money">{formatUsd(row.usd_amount)}</span>
                <span className="u-money-secondary">
                  {formatLocal(row.amount, row.currency, minorUnits.get(row.currency))}
                </span>
                {row.change_reason && (
                  <Badge size="sm" color="neutral">
                    {REASON_LABELS[row.change_reason] ?? row.change_reason}
                  </Badge>
                )}
                {current && (
                  <Badge size="sm" color="brand">
                    Current
                  </Badge>
                )}
              </div>

              <span className={styles.range}>
                {formatDate(row.effective_from)} —{' '}
                {row.effective_to ? formatDate(row.effective_to) : 'present'}
              </span>

              {/* Who decided it and when they decided it. Distinct from the range
                  above, which is when the money changed. */}
              <span className={styles.meta}>
                {row.created_by_name ? `by ${row.created_by_name}` : 'by —'} · recorded{' '}
                {formatDate(row.created_at)} · {row.currency} at {row.fx_units_per_usd} per USD
              </span>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
