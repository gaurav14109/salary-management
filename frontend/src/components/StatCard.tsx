/**
 * One headline number.
 *
 * The unit is part of the number, not decoration: an unlabelled 180,018 beside a
 * column of rupee figures is a number someone will read as rupees.
 */

import { Card, Text } from '@mantine/core'
import type { ReactNode } from 'react'

export function StatCard({
  label,
  value,
  unit,
  hint,
  icon,
}: {
  label: string
  value: string
  unit?: string
  hint?: ReactNode
  icon?: ReactNode
}) {
  return (
    <Card>
      <div className="u-row-between">
        <span className="u-label">{label}</span>
        {icon}
      </div>
      <div className="u-money-large" style={{ marginTop: 'var(--space-2)' }}>
        {value}
        {unit && (
          <Text component="span" size="sm" c="dimmed" fw={500} ml={6}>
            {unit}
          </Text>
        )}
      </div>
      {hint && (
        <Text size="xs" c="dimmed" mt={4}>
          {hint}
        </Text>
      )}
    </Card>
  )
}
