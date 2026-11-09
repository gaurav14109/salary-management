/**
 * Reading a server-sent event stream from a POST.
 *
 * `EventSource` only does GET, and the question has to go in a body - so the stream
 * is read by hand. The parsing is where the bugs are: chunks do not arrive on event
 * boundaries, so one `data:` line routinely arrives in two pieces and two events
 * routinely arrive in one.
 */

import { describe, expect, it, vi } from 'vitest'

import { streamChat } from './stream'

function responseOf(...chunks: string[]) {
  const encoder = new TextEncoder()
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        let i = 0
        return {
          read: async () =>
            i < chunks.length
              ? { done: false, value: encoder.encode(chunks[i++]) }
              : { done: true, value: undefined },
          releaseLock() {},
        }
      },
    },
  } as unknown as Response
}

async function collect(response: Response) {
  const events: unknown[] = []
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
  await streamChat('q', (event) => events.push(event))
  return events
}

describe('streamChat', () => {
  it('yields one event per data line', async () => {
    const events = await collect(
      responseOf(
        'data: {"type":"token","text":"Total "}\n\n',
        'data: {"type":"token","text":"payroll"}\n\n',
        'data: {"type":"done","used_tools":[],"figures":[]}\n\n',
      ),
    )

    expect(events).toEqual([
      { type: 'token', text: 'Total ' },
      { type: 'token', text: 'payroll' },
      { type: 'done', used_tools: [], figures: [] },
    ])
  })

  it('reassembles an event split across two chunks', async () => {
    /* The network does not respect event boundaries. Parsing per chunk drops the
       half-line and the answer silently loses words. */
    const events = await collect(
      responseOf('data: {"type":"tok', 'en","text":"hello"}\n\n'),
    )

    expect(events).toEqual([{ type: 'token', text: 'hello' }])
  })

  it('handles two events arriving in one chunk', async () => {
    const events = await collect(
      responseOf('data: {"type":"token","text":"a"}\n\ndata: {"type":"token","text":"b"}\n\n'),
    )

    expect(events).toHaveLength(2)
  })

  it('ignores keep-alive comments and blank lines', async () => {
    const events = await collect(
      responseOf(': keep-alive\n\n', 'data: {"type":"token","text":"x"}\n\n'),
    )

    expect(events).toEqual([{ type: 'token', text: 'x' }])
  })

  it('skips a malformed line rather than ending the answer', async () => {
    const events = await collect(
      responseOf('data: {broken\n\n', 'data: {"type":"token","text":"ok"}\n\n'),
    )

    expect(events).toEqual([{ type: 'token', text: 'ok' }])
  })

  it('sends the CSRF header, because this is a POST', async () => {
    document.cookie = 'salary_csrf=tok123'
    const fetchMock = vi.fn().mockResolvedValue(responseOf('data: {"type":"done"}\n\n'))
    vi.stubGlobal('fetch', fetchMock)

    await streamChat('q', () => {})

    expect(fetchMock.mock.calls[0][1].headers['X-CSRF-Token']).toBe('tok123')
    expect(fetchMock.mock.calls[0][1].credentials).toBe('include')
  })

  it('reports a refused request as an error event rather than throwing', async () => {
    /* The widget has to render something. An unhandled rejection in a stream
       leaves the panel showing a spinner for ever. */
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ detail: 'You do not have permission.' }),
      } as Response),
    )
    const events: unknown[] = []

    await streamChat('q', (event) => events.push(event))

    expect(events).toEqual([{ type: 'error', detail: 'You do not have permission.' }])
  })

  it('reports an unreachable server as an error event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    const events: unknown[] = []

    await streamChat('q', (event) => events.push(event))

    expect(events[0]).toMatchObject({ type: 'error' })
  })
})
