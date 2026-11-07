import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import { HealthPage } from './HealthPage'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, get: vi.fn() } }
})

const { api } = await import('../api/client')
const get = vi.mocked(api.get)

afterEach(() => vi.resetAllMocks())

describe('HealthPage', () => {
  it('shows the status returned through the BFF', async () => {
    get.mockResolvedValue({ status: 'ok' })

    render(<HealthPage />)

    expect(await screen.findByText(/backend reachable/i)).toBeInTheDocument()
  })

  it('shows a readable message when the backend cannot be reached', async () => {
    get.mockRejectedValue(new ApiError(0, 'Could not reach the server'))

    render(<HealthPage />)

    expect(await screen.findByText(/could not reach the server/i)).toBeInTheDocument()
  })

  it('shows a checking state before the request resolves', () => {
    get.mockReturnValue(new Promise(() => {}))

    render(<HealthPage />)

    expect(screen.getByText(/checking/i)).toBeInTheDocument()
  })
})
