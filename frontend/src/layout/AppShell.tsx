/**
 * The frame every page sits in.
 *
 * Navigation is role-aware, which is good manners rather than a control: the salary
 * service decides what an HR manager may do, and decides it again on every request.
 * Hiding a link they would be refused saves them a dead end; it does not protect
 * anything.
 */

import { Avatar, Badge, Button, Group, Text } from '@mantine/core'
import {
  IconChartBar,
  IconCurrencyDollar,
  IconLogout,
  IconUpload,
  IconUsers,
} from '@tabler/icons-react'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

import { useAuth } from '../auth/useAuth'
import styles from './AppShell.module.css'

type Destination = {
  to: string
  label: string
  icon: ReactNode
  /** Absent means both roles. */
  requiresRole?: string
}

const DESTINATIONS: Destination[] = [
  { to: '/employees', label: 'Employees', icon: <IconUsers size={18} stroke={1.6} /> },
  { to: '/dashboard', label: 'Dashboard', icon: <IconChartBar size={18} stroke={1.6} /> },
  { to: '/import', label: 'Import', icon: <IconUpload size={18} stroke={1.6} /> },
  {
    to: '/fx-rates',
    label: 'FX Rates',
    icon: <IconCurrencyDollar size={18} stroke={1.6} />,
    // Admin only. A rate moves every reported figure in its currency without
    // touching a salary, which is a larger power than editing one person's pay.
    requiresRole: 'admin',
  },
]

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin',
  hr_manager: 'HR Manager',
}

export function AppShell({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const user = auth.status === 'authenticated' ? auth.user : null
  const visible = DESTINATIONS.filter((d) => !d.requiresRole || auth.hasRole(d.requiresRole))

  return (
    <div className={styles.shell}>
      <div className={styles.brand}>
        <span className={styles.brandMark} aria-hidden="true">
          A
        </span>
        <span>ACME Salary</span>
      </div>

      <header className={styles.header}>
        {user && (
          <Group gap="xs">
            {/* Roles shown, not inferred. Someone who holds both should see both -
                "why can I do this here and not there" is a support ticket. */}
            {user.roles
              .filter((role) => role in ROLE_LABELS)
              .map((role) => (
                <Badge key={role} color={role === 'admin' ? 'brand' : 'neutral'} size="sm">
                  {ROLE_LABELS[role]}
                </Badge>
              ))}
            <Avatar size={28} radius="xl" color="brand">
              {user.username.slice(0, 2).toUpperCase()}
            </Avatar>
            <Text size="sm" fw={500}>
              {user.username}
            </Text>
          </Group>
        )}
        <Button
          variant="subtle"
          color="neutral"
          size="sm"
          leftSection={<IconLogout size={16} stroke={1.6} />}
          onClick={() => void auth.logout()}
        >
          Sign out
        </Button>
      </header>

      <nav className={styles.nav} aria-label="Sections">
        {visible.map((destination) => (
          <NavLink
            key={destination.to}
            to={destination.to}
            className={({ isActive }) =>
              isActive ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink
            }
            // NavLink sets aria-current="page" itself when active, so the current
            // section is announced as well as tinted - "which page am I on" must not
            // depend on seeing a colour.
          >
            {destination.icon}
            <span>{destination.label}</span>
          </NavLink>
        ))}
      </nav>

      <main className={styles.main}>{children}</main>
    </div>
  )
}
