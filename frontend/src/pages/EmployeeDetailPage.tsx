/**
 * One person: what they are paid now, and how it got that way.
 *
 * Money appears with its rate everywhere. Two people on identical local pay can show
 * different USD figures, because each row froze the rate in force when it was
 * written - so a converted number with nothing to explain it is the first thing an
 * HR manager distrusts, correctly.
 */

import { Badge, Button, Card, Group, Loader, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconAlertTriangle, IconArrowLeft, IconPencil } from '@tabler/icons-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { employees, reference } from '../api/resources'
import { useAuth } from '../auth/useAuth'
import { SalaryEditForm, type SalaryFormValues } from '../components/SalaryEditForm'
import { SalaryTimeline } from '../components/SalaryTimeline'
import { formatDate, formatLocal, formatUsd } from '../format/money'
import styles from './EmployeeDetailPage.module.css'

export function EmployeeDetailPage() {
  const { id } = useParams()
  const employeeId = Number(id)
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)

  const { data: refData } = useQuery({
    queryKey: ['reference'],
    queryFn: reference.all,
    staleTime: Infinity,
  })

  const { data, isPending, isError } = useQuery({
    queryKey: ['employee', employeeId],
    queryFn: () => employees.detail(employeeId),
    enabled: Number.isFinite(employeeId),
  })

  const minorUnits = useMemo(() => {
    const map = new Map<string, number>()
    refData?.currencies.forEach((c) => map.set(c.code, c.minor_units))
    return map
  }, [refData])

  const changeSalary = useMutation({
    mutationFn: (values: SalaryFormValues) =>
      employees.setSalary(employeeId, {
        amount: values.amount,
        currency: values.currency,
        effective_from: values.effective_from,
        ...(values.change_reason ? { change_reason: values.change_reason } : {}),
      }),
    onSuccess: (result) => {
      setEditing(false)
      // The API distinguishes "no change" from "updated", and so must this. A green
      // success toast after a no-op is the interface telling a comfortable lie.
      if (result.changed) {
        notifications.show({
          color: 'positive',
          title: 'Salary updated',
          message: `Now ${formatUsd(result.salary.usd_amount)}.`,
        })
      } else {
        notifications.show({
          color: 'neutral',
          title: 'No change',
          message: 'That is already their current pay, so nothing was recorded.',
        })
      }
      void queryClient.invalidateQueries({ queryKey: ['employee', employeeId] })
      // The list shows current pay too, and a stale figure there after an edit here
      // is the user wondering which screen to believe.
      void queryClient.invalidateQueries({ queryKey: ['employees'] })
    },
  })

  if (isError) {
    return (
      <div className="u-page">
        <div className="u-empty" role="alert">
          <IconAlertTriangle size={28} stroke={1.5} />
          <Text fw={500}>Could not load this employee</Text>
          <Button component={Link} to="/employees" variant="default" size="sm">
            Back to employees
          </Button>
        </div>
      </div>
    )
  }

  if (isPending || !data) {
    return (
      <div className="u-page">
        <Loader size="sm" />
      </div>
    )
  }

  const { employee, current_salary: current, history } = data
  const departmentName = refData?.departments.find((d) => d.id === employee.department_id)?.name
  const bandName = refData?.bands.find((b) => b.id === employee.band_id)?.name
  const countryName = refData?.countries.find((c) => c.code === employee.country_code)?.name
  const active = employee.status === 'ACTIVE'

  return (
    <div className="u-page u-stack">
      <Button
        component={Link}
        to="/employees"
        variant="subtle"
        color="neutral"
        size="compact-sm"
        leftSection={<IconArrowLeft size={15} stroke={1.6} />}
        w="fit-content"
      >
        Employees
      </Button>

      <div className={styles.header}>
        <div className="u-stack-tight">
          <Group gap="xs">
            <h1 className="u-title">
              {employee.first_name} {employee.last_name}
            </h1>
            {!active && (
              <Badge color="neutral" size="sm">
                Left {formatDate(employee.termination_date)}
              </Badge>
            )}
          </Group>
          <span className="u-muted">
            {employee.employee_code} · {employee.job_title}
          </span>
        </div>

        <Group gap="xs">
          {/* Two conditions, two different reasons. An admin is a read-only role
              and may not change pay at all. Someone who has left is refused by the
              service whoever asks - and offering a button that fails at 422 is
              worse manners than not offering it. */}
          {auth.hasRole('hr_manager') && active && (
            <Button
              leftSection={<IconPencil size={16} stroke={1.6} />}
              onClick={() => setEditing(true)}
            >
              Change salary
            </Button>
          )}
        </Group>
      </div>

      <div className={styles.columns}>
        <div className="u-stack">
          <Card>
            <span className="u-label">Current salary</span>
            {current ? (
              <>
                <div className="u-money-large">{formatUsd(current.usd_amount)}</div>
                <div className="u-money-secondary">
                  {formatLocal(current.amount, current.currency, minorUnits.get(current.currency))}{' '}
                  {current.currency}
                </div>
                <div className={styles.rateLine}>
                  {/* The rate and its date, published rather than hidden. This is
                      what makes the USD figure above explainable. */}
                  <span className="u-faint">
                    Converted at {current.fx_units_per_usd} {current.currency} per USD
                  </span>
                  <span className="u-faint">Rate dated {formatDate(current.fx_rate_date)}</span>
                  <span className="u-faint">
                    Effective from {formatDate(current.effective_from)}
                  </span>
                </div>
              </>
            ) : (
              <Text size="sm" c="dimmed" mt="xs">
                No salary recorded yet.
              </Text>
            )}
          </Card>

          <Card>
            <span className="u-label">Details</span>
            <div className={styles.facts} style={{ marginTop: 'var(--space-3)' }}>
              <span className={styles.factLabel}>Department</span>
              <span>{departmentName ?? '—'}</span>
              <span className={styles.factLabel}>Band</span>
              <span>{bandName ?? '—'}</span>
              <span className={styles.factLabel}>Country</span>
              <span>{countryName ?? employee.country_code}</span>
              <span className={styles.factLabel}>Email</span>
              <span>{employee.email ?? '—'}</span>
              <span className={styles.factLabel}>Hired</span>
              <span>{formatDate(employee.hire_date)}</span>
            </div>
          </Card>
        </div>

        <Card>
          <span className="u-label">Salary history</span>
          <div style={{ marginTop: 'var(--space-4)' }}>
            <SalaryTimeline history={history} minorUnits={minorUnits} />
          </div>
        </Card>
      </div>

      <SalaryEditForm
        opened={editing}
        onClose={() => setEditing(false)}
        onSubmit={(values) => changeSalary.mutate(values)}
        current={current}
        currencies={refData?.currencies ?? []}
        submitting={changeSalary.isPending}
        error={
          changeSalary.error instanceof ApiError
            ? changeSalary.error.message
            : changeSalary.error
              ? 'Could not save that change.'
              : null
        }
      />
    </div>
  )
}
