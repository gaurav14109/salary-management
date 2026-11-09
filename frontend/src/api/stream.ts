/**
 * Reading the assistant's answer as it is written.
 *
 * `EventSource` is the obvious tool and cannot be used: it only does GET, and the
 * question has to travel in a body. So the stream is read by hand - which is fine,
 * but it puts the parsing here, and the parsing is where the bugs are.
 *
 * The one that matters: chunks do not arrive on event boundaries. A single
 * `data: {...}` line routinely arrives in two pieces, and two events routinely
 * arrive in one chunk. Parsing each chunk on its own drops the split line, and the
 * answer quietly loses words - which looks like the model being terse rather than
 * like a bug.
 */

import { csrfToken } from './client'

export type ChatEvent =
  | { type: 'status'; tool: string }
  | { type: 'token'; text: string }
  | { type: 'done'; used_tools: string[]; figures: { tool: string; result: unknown }[] }
  | { type: 'error'; detail: string }

/**
 * POST a question and call `onEvent` for each event until the stream ends.
 *
 * Never throws. The widget has to render *something*, and an unhandled rejection
 * mid-stream leaves a panel showing a spinner for ever - so failures arrive as a
 * final `error` event, in the same channel as everything else.
 */
export async function streamChat(
  question: string,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    const token = csrfToken()
    response = await fetch('/chat/ask/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'X-CSRF-Token': token } : {}),
      },
      body: JSON.stringify({ question }),
      // Without this the session cookie is not sent and the BFF answers 401.
      credentials: 'include',
      signal,
    })
  } catch {
    onEvent({ type: 'error', detail: 'Could not reach the assistant.' })
    return
  }

  if (!response.ok) {
    // A refusal arrives as an ordinary JSON body, not as a stream.
    let detail = 'The assistant is unavailable right now.'
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // A proxy error returns HTML or nothing; the default sentence stands.
    }
    onEvent({ type: 'error', detail })
    return
  }

  const reader = response.body?.getReader()
  if (!reader) {
    onEvent({ type: 'error', detail: 'The assistant sent nothing.' })
    return
  }

  const decoder = new TextDecoder()
  // Whatever arrived after the last complete event, waiting for the rest of itself.
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Events are separated by a blank line. Everything up to the last separator
      // is complete; the remainder stays in the buffer.
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        const event = parse(part)
        if (event) onEvent(event)
      }
    }
    const last = parse(buffer)
    if (last) onEvent(last)
  } catch {
    onEvent({ type: 'error', detail: 'The answer was interrupted.' })
  } finally {
    reader.releaseLock()
  }
}

/** One SSE block into an event, or nothing if it is a comment or malformed. */
function parse(block: string): ChatEvent | null {
  const line = block.split('\n').find((candidate) => candidate.startsWith('data:'))
  if (!line) return null

  const payload = line.slice('data:'.length).trim()
  if (!payload || payload === '[DONE]') return null

  try {
    return JSON.parse(payload) as ChatEvent
  } catch {
    // A truncated line must not end the answer. The next one may be fine.
    return null
  }
}
