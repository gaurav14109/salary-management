# The assistant

Answers questions about ACME payroll from whitelisted, read-only tools.

A separate service, and separate for a reason: it holds no salary data and has no
database credentials for any. It reads through the salary API with the asking user's
own claims, so the role checks that protect the UI protect the assistant too, and
there is one data-access path to audit rather than two.

## How a question is answered

```
browser → BFF (verifies session) → chat service → model (Groq)
                                        ↓ tool calls
                                   salary API (as the asking user)
```

1. The BFF proxies `/chat/ask` here with the caller's claims as headers.
2. Reference notes matching the question are added to the system prompt.
3. The model is offered the tool list and asked to answer.
4. Any tool it asks for is looked up in `REGISTRY`; anything else is refused.
5. Tools call the salary API over HTTP, as that user.
6. Results go back to the model, which writes the answer.

At most 3 rounds of tool calls, then it answers with what it has.

## What makes it safe

Structural, not prompted — a system prompt is a request, and a model under a
determined question will grant it.

| Guarantee | How |
|---|---|
| Cannot run anything not on the menu | Name looked up in a dict; no path from a generated string to a function |
| Cannot write | Every tool is a GET. There is no write tool to call |
| Cannot read past the user's role | Tools carry the user's claims; the salary API enforces them |
| Cannot write SQL | No SQL is generated, no table names are in the prompt |
| Cannot dump the payroll | `top_paid` and `lowest_paid` cap at 10 people, no contact details |
| Cannot invent a figure | Answers only from tool results; told to refuse rather than estimate |

## Tools

`get_dashboard_summary` · `count_employees` · `top_paid` · `lowest_paid` ·
`list_countries` · `list_departments` · `list_bands`

## Configuration

```
GATEWAY_KEY     # must match the BFF's and the salary API's
SALARY_API_URL  # http://api:8000 on the compose network
GROQ_API_KEY    # https://console.groq.com/keys - free tier
GROQ_MODEL      # default openai/gpt-oss-120b
```

Without `GROQ_API_KEY` the service starts, logs a warning, and answers every
question with "the assistant is not configured". The rest of the product does not
depend on it, so an absent key must not stop the stack.

## Running the tests

```bash
cd chat
PYTHONPATH=. pytest -q
```

The model and the salary API are both faked. What is tested is the harness — which
tool ran, what it was asked for, when the loop stops — not whether a language model
is any good, which no test here could establish.

## Known gap

The sort key `top_paid` uses (`usd_amount`) is the salary API's column name, and
nothing in this repo stops the two drifting apart: a fake cannot know the API's
allow-list. It was wrong once and every `top_paid` question failed with a 422. The
guard is running the tools against the real API before shipping — a contract test
would need the two services to share a package, which is the coupling this split
exists to avoid.
