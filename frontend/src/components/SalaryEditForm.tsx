/**
 * Changing someone's pay.
 *
 * The form collects what the person is paid - a local amount, a currency, a date it
 * starts from. It never collects a USD figure: `SalaryWrite` refuses one on purpose,
 * because a browser that can supply `usd_amount` can decide what every reported
 * total says.
 *
 * It also stays open when the server refuses. A dialog that closes on error throws
 * away what was typed and leaves the user guessing what happened.
 */

import { Alert, Button, Group, Modal, Select, Stack, TextInput } from '@mantine/core'
import { useForm } from '@mantine/form'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useState } from 'react'

import type { Currency, Salary } from '../api/types'

const REASONS = [
  { value: 'MERIT', label: 'Merit' },
  { value: 'PROMOTION', label: 'Promotion' },
  { value: 'MARKET_ADJUSTMENT', label: 'Market adjustment' },
  { value: 'NEW_HIRE', label: 'New hire' },
  { value: 'CORRECTION', label: 'Correction' },
]

export type SalaryFormValues = {
  amount: string
  currency: string
  effective_from: string
  change_reason: string | null
}

export function SalaryEditForm({
  opened,
  onClose,
  onSubmit,
  current,
  currencies,
  submitting,
  error,
}: {
  opened: boolean
  onClose: () => void
  onSubmit: (values: SalaryFormValues) => void
  current: Salary | null
  currencies: Currency[]
  submitting: boolean
  error: string | null
}) {
  const [touched, setTouched] = useState(false)

  const form = useForm<SalaryFormValues>({
    initialValues: {
      // Pre-filled with what they earn now: most changes are an edit to a number,
      // not a fresh entry, and retyping an eight-digit salary invites a typo.
      amount: current?.amount ?? '',
      currency: current?.currency ?? 'USD',
      effective_from: '',
      change_reason: null,
    },
    validate: {
      amount: (value) => {
        if (!value.trim()) return 'Enter an amount'
        const numeric = Number(value.replace(/,/g, ''))
        if (Number.isNaN(numeric)) return 'Amount must be a number'
        if (numeric <= 0) return 'Amount must be greater than zero'
        return null
      },
      effective_from: (value) => (value ? null : 'Choose the date this pay starts'),
    },
  })

  function close() {
    form.reset()
    setTouched(false)
    onClose()
  }

  return (
    <Modal opened={opened} onClose={close} title="Change salary" size="md">
      <form
        onSubmit={form.onSubmit((values) => {
          setTouched(true)
          onSubmit({ ...values, amount: values.amount.replace(/,/g, '').trim() })
        })}
      >
        <Stack gap="md">
          {/* The server's refusal, verbatim. "No FX rate for XAF as of 2026-04-01"
              tells an admin exactly what to add; "Request failed" tells nobody
              anything. */}
          {error && touched && (
            <Alert color="critical" icon={<IconAlertTriangle size={16} />} variant="light">
              {error}
            </Alert>
          )}

          <TextInput
            label="Amount"
            description="In the currency this person is paid in."
            placeholder="1200000"
            withAsterisk
            {...form.getInputProps('amount')}
          />

          <Select
            label="Currency"
            withAsterisk
            searchable
            data={currencies.map((c) => ({ value: c.code, label: `${c.code} — ${c.name}` }))}
            {...form.getInputProps('currency')}
          />

          <TextInput
            type="date"
            label="Effective from"
            description="The day this pay starts. The previous salary is closed the day before."
            withAsterisk
            {...form.getInputProps('effective_from')}
          />

          <Select
            label="Reason"
            placeholder="Optional"
            clearable
            data={REASONS}
            {...form.getInputProps('change_reason')}
          />

          <Group justify="flex-end" gap="xs">
            <Button variant="default" onClick={close} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              Save change
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  )
}
