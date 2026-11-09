/**
 * The assistant, as a panel in the corner.
 *
 * It sits on every page, so the bar for it being unobtrusive is high: closed by
 * default, one button, and nothing on screen until somebody asks for it.
 *
 * The streaming client is mocked. What these cover is what the user sees - that an
 * answer appears as it is written, that the slow part says what it is doing, and
 * that a failure leaves something readable rather than a spinner.
 */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChatWidget } from './ChatWidget'
import type { ChatEvent } from '../api/stream'
import * as stream from '../api/stream'
import { renderPage } from '../test/render'

function replies(...events: ChatEvent[]) {
  return vi
    .spyOn(stream, 'streamChat')
    .mockImplementation(async (_question, onEvent) => {
      for (const event of events) onEvent(event)
    })
}

const DONE: ChatEvent = { type: 'done', used_tools: [], figures: [] }

async function ask(user: ReturnType<typeof userEvent.setup>, question = 'total payroll?') {
  await user.click(screen.getByRole('button', { name: /ask the assistant/i }))
  await user.type(screen.getByRole('textbox', { name: /question/i }), question)
  await user.click(screen.getByRole('button', { name: /^send$/i }))
}

afterEach(() => vi.restoreAllMocks())

describe('ChatWidget', () => {
  it('starts closed, as one button', () => {
    renderPage(<ChatWidget />)

    expect(screen.getByRole('button', { name: /ask the assistant/i })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /question/i })).not.toBeInTheDocument()
  })

  it('opens when the button is pressed', async () => {
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await user.click(screen.getByRole('button', { name: /ask the assistant/i }))

    expect(screen.getByRole('textbox', { name: /question/i })).toBeInTheDocument()
  })

  it('closes again without losing the conversation', async () => {
    /* Closing the panel to read the page underneath is the common case. Throwing
       the answer away would make people retype the question. */
    replies({ type: 'token', text: 'ACME has 42 employees.' }, DONE)
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)
    await screen.findByText('ACME has 42 employees.')
    await user.click(screen.getByRole('button', { name: /close/i }))
    await user.click(screen.getByRole('button', { name: /ask the assistant/i }))

    expect(screen.getByText('ACME has 42 employees.')).toBeInTheDocument()
  })

  it('shows the question that was asked', async () => {
    replies(DONE)
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user, 'how many people in India?')

    expect(screen.getByText('how many people in India?')).toBeInTheDocument()
  })

  it('builds the answer up as the tokens arrive', async () => {
    replies(
      { type: 'token', text: 'Total ' },
      { type: 'token', text: 'payroll is ' },
      { type: 'token', text: '$1,916,868,391.00.' },
      DONE,
    )
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)

    expect(await screen.findByText('Total payroll is $1,916,868,391.00.')).toBeInTheDocument()
  })

  it('says what it is doing while a tool runs', async () => {
    /* The tool round is the slow part - silence for several seconds reads as
       broken. The status is transient by design: the first token replaces it, so
       the stream is held open here to catch it on screen. */
    vi.spyOn(stream, 'streamChat').mockImplementation(async (_question, onEvent) => {
      onEvent({ type: 'status', tool: 'get_dashboard_summary' })
      await new Promise(() => {})
    })
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)

    expect(await screen.findByText(/checking the payroll figures/i)).toBeInTheDocument()
  })

  it('clears the status line once the answer starts arriving', async () => {
    replies({ type: 'status', tool: 'get_dashboard_summary' }, { type: 'token', text: 'ok' }, DONE)
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)

    await screen.findByText('ok')
    expect(screen.queryByText(/checking the payroll figures/i)).not.toBeInTheDocument()
  })

  it('names the tools the answer came from', async () => {
    /* Provenance. A figure with no source is a figure nobody can check, and this
       is a product where being quietly wrong is worse than being unavailable. */
    replies(
      { type: 'token', text: '19,483 employees.' },
      { type: 'done', used_tools: ['count_employees'], figures: [] },
    )
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)

    expect(await screen.findByText(/count_employees/)).toBeInTheDocument()
  })

  it('shows an error in the conversation rather than a stuck spinner', async () => {
    replies({ type: 'error', detail: 'The assistant is busy right now.' })
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)

    expect(await screen.findByText('The assistant is busy right now.')).toBeInTheDocument()
  })

  it('will not send an empty question', async () => {
    const spy = replies(DONE)
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await user.click(screen.getByRole('button', { name: /ask the assistant/i }))
    await user.click(screen.getByRole('button', { name: /^send$/i }))

    expect(spy).not.toHaveBeenCalled()
  })

  it('clears the box once the question is sent', async () => {
    replies(DONE)
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)

    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: /question/i })).toHaveValue(''),
    )
  })

  it('refuses a second question while one is still streaming', async () => {
    /* Two answers interleaving into one bubble is unreadable, and the second
       question costs a call against a rate-limited free tier. */
    const spy = vi.spyOn(stream, 'streamChat').mockImplementation(() => new Promise(() => {}))
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await ask(user)
    await user.type(screen.getByRole('textbox', { name: /question/i }), 'another')
    await user.click(screen.getByRole('button', { name: /^send$/i }))

    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('sends on Enter', async () => {
    const spy = replies(DONE)
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await user.click(screen.getByRole('button', { name: /ask the assistant/i }))
    await user.type(screen.getByRole('textbox', { name: /question/i }), 'how many people?{Enter}')

    expect(spy).toHaveBeenCalledWith('how many people?', expect.any(Function), expect.anything())
  })

  it('suggests what it can answer when the conversation is empty', async () => {
    /* An empty box with a blinking cursor gets "hi" typed into it. Examples get a
       real question typed into it. */
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await user.click(screen.getByRole('button', { name: /ask the assistant/i }))

    expect(screen.getByText(/total payroll/i)).toBeInTheDocument()
  })

  it('asks a suggestion when it is clicked', async () => {
    const spy = replies(DONE)
    const user = userEvent.setup()
    renderPage(<ChatWidget />)

    await user.click(screen.getByRole('button', { name: /ask the assistant/i }))
    await user.click(screen.getByRole('button', { name: /total payroll/i }))

    expect(spy).toHaveBeenCalled()
  })
})
