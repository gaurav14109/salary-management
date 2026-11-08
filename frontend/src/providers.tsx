/**
 * Everything the app needs wrapped around it, in the order it needs wrapping.
 *
 * Split from App so tests can mount a page with real providers and a fake session,
 * rather than each test file reassembling the stack and drifting from production.
 */

import { MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { type QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { createQueryClient } from './queryClient'
import { cssVariablesResolver, theme } from './theme/theme'

export function Providers({
  children,
  client = createQueryClient(),
}: {
  children: ReactNode
  client?: QueryClient
}) {
  return (
    <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} defaultColorScheme="light">
      {/* Top right, out of the way of the primary action, which sits bottom-right in
          every modal in this app. */}
      <Notifications position="top-right" limit={3} />
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </MantineProvider>
  )
}
