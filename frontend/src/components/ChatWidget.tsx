/**
 * The assistant, in the corner of every page.
 *
 * Deliberately small. It sits on top of whatever the HR Manager is actually doing,
 * so closed it is one button, and open it is a panel that can be dismissed without
 * losing the conversation - closing it to read the table underneath is the common
 * case, and throwing the answer away would make people retype the question.
 *
 * The answer is streamed. Prose arrives a few characters at a time and is appended
 * to the last message, which is what makes a three-second answer feel like one
 * being written rather than one being waited for. The slow part - a tool round -
 * says what it is doing, because several seconds of silence reads as broken.
 *
 * Every figure it reports comes from the salary API, read as the signed-in user.
 * The footer names the tools behind an answer for the same reason the dashboard
 * labels its units: a number with no provenance is a number nobody can check.
 */

import { ActionIcon, Button, Loader, Text, Textarea } from '@mantine/core'
import { IconMessageCircle, IconSend, IconX } from '@tabler/icons-react'
import { useEffect, useRef, useState } from 'react'

import { streamChat, type ChatEvent } from '../api/stream'
import styles from './ChatWidget.module.css'

type Message = {
  role: 'user' | 'assistant'
  text: string
  tools?: string[]
  failed?: boolean
}

/** What a tool is doing, in words an HR Manager reads rather than a function name. */
const DOING: Record<string, string> = {
  get_dashboard_summary: 'Checking the payroll figures…',
  count_employees: 'Counting employees…',
  top_paid: 'Looking up the highest paid…',
  lowest_paid: 'Looking up the lowest paid…',
  list_countries: 'Checking the country list…',
  list_departments: 'Checking the departments…',
  list_bands: 'Checking the bands…',
}

/**
 * An empty box with a blinking cursor gets "hi" typed into it. Examples get a real
 * question typed into it - and these three are the ones the tools answer best.
 */
const SUGGESTIONS = [
  'What is our total payroll?',
  'How many people do we employ in India?',
  'What is the median salary?',
]

export function ChatWidget() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [doing, setDoing] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Follow the answer as it is written, the way a chat should.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [messages, doing])

  // A page change should not leave a stream running against a panel nobody can see.
  useEffect(() => () => abortRef.current?.abort(), [])

  const send = (text: string) => {
    const asked = text.trim()
    // Two answers interleaving into one bubble is unreadable, and the second
    // question costs a call against a rate-limited free tier.
    if (!asked || busy) return

    setMessages((current) => [
      ...current,
      { role: 'user', text: asked },
      { role: 'assistant', text: '' },
    ])
    setQuestion('')
    setBusy(true)
    setDoing(null)

    const controller = new AbortController()
    abortRef.current = controller

    void streamChat(asked, (event) => onEvent(event, setMessages, setDoing), controller.signal)
      .finally(() => {
        setBusy(false)
        setDoing(null)
      })
  }

  if (!open) {
    return (
      <div className={styles.anchor}>
        <ActionIcon
          className={styles.launcher}
          aria-label="Ask the assistant"
          onClick={() => setOpen(true)}
          size={52}
          radius="xl"
          variant="filled"
        >
          <IconMessageCircle size={24} stroke={1.6} />
        </ActionIcon>
      </div>
    )
  }

  return (
    <div className={styles.anchor}>
      <section className={styles.panel} aria-label="Assistant">
      <header className={styles.header}>
        <div>
          <Text fw={600} size="sm">
            Assistant
          </Text>
          <Text size="xs" c="dimmed">
            Answers from your payroll data
          </Text>
        </div>
        <ActionIcon variant="subtle" aria-label="Close" onClick={() => setOpen(false)}>
          <IconX size={18} />
        </ActionIcon>
      </header>

      <div className={styles.log}>
        {messages.length === 0 && (
          <div className={styles.suggestions}>
            <Text size="xs" c="dimmed">
              Try asking:
            </Text>
            {SUGGESTIONS.map((suggestion) => (
              <Button
                key={suggestion}
                variant="default"
                size="compact-sm"
                onClick={() => send(suggestion)}
              >
                {suggestion}
              </Button>
            ))}
          </div>
        )}

        {messages.map((message, index) => (
          <div
            key={index}
            className={message.role === 'user' ? styles.fromUser : styles.fromAssistant}
          >
            {message.text && (
              <Text size="sm" className={message.failed ? styles.failed : undefined}>
                {message.text}
              </Text>
            )}
            {message.tools && message.tools.length > 0 && (
              <Text size="xs" c="dimmed" className={styles.provenance}>
                from {message.tools.join(', ')}
              </Text>
            )}
          </div>
        ))}

        {doing && (
          <div className={styles.doing}>
            <Loader size="xs" />
            <Text size="xs" c="dimmed">
              {doing}
            </Text>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className={styles.composer}>
        <Textarea
          aria-label="Your question"
          placeholder="Ask about headcount, salaries, averages…"
          value={question}
          onChange={(event) => setQuestion(event.currentTarget.value)}
          onKeyDown={(event) => {
            // Enter sends; shift+Enter is a new line, as every chat box works.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              send(question)
            }
          }}
          // Fixed rows rather than Mantine's `autosize`: that variant reaches for
          // a window reference to measure against, which is one more moving part
          // for a box that is two lines tall either way.
          rows={2}
          disabled={busy}
        />
          <Button
            onClick={() => send(question)}
            loading={busy}
            leftSection={<IconSend size={15} />}
          >
            Send
          </Button>
        </div>
      </section>
    </div>
  )
}

/**
 * Fold one event into the conversation.
 *
 * Tokens append to the last message rather than creating one, which is what makes
 * the answer look written rather than delivered.
 */
function onEvent(
  event: ChatEvent,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  setDoing: (text: string | null) => void,
) {
  if (event.type === 'status') {
    setDoing(DOING[event.tool] ?? 'Looking that up…')
    return
  }

  if (event.type === 'token') {
    setDoing(null)
    setMessages((current) => appendToLast(current, (last) => ({ ...last, text: last.text + event.text })))
    return
  }

  if (event.type === 'done') {
    setMessages((current) =>
      appendToLast(current, (last) => ({ ...last, tools: event.used_tools })),
    )
    return
  }

  // An error is shown in the conversation, not as a toast: it is the answer to the
  // question that was just asked, and it belongs where the answer would have been.
  setMessages((current) =>
    appendToLast(current, (last) => ({ ...last, text: event.detail, failed: true })),
  )
}

function appendToLast(messages: Message[], update: (last: Message) => Message): Message[] {
  if (messages.length === 0) return messages
  const last = messages[messages.length - 1]
  if (last.role !== 'assistant') return messages
  return [...messages.slice(0, -1), update(last)]
}
