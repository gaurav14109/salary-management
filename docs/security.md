# Security

Salary data is the kind people get fired over. This is what protects it, written
plainly, following the OWASP Top 10.

Each section says the risk in one line, then what was done about it.

---

## A01 · Broken access control

**The risk:** someone does a thing they are not allowed to do.

- There are two roles. **The HR manager writes. The admin only reads.**
- The role is checked **before** the request body is even read, so a wrong role is
  turned away before anything else happens.
- One test walks **every route in the app** and fails if a write lets an admin in.
  Another fails if a write has no role check at all. So the next endpoint someone
  adds cannot quietly get this wrong.
- The UI also hides what you cannot do — but that is politeness, not protection.
  The server refuses either way.
- **After login you can only be sent to a page inside this app.** Otherwise a link
  to our real site could drop you on someone else's after a genuine sign-in.
- The BFF refuses odd paths like `/api/../../admin` instead of trying to tidy them
  up. Guessing what someone meant is how you forward a request nobody meant to send.

## A02 · Weak or missing encryption

**The risk:** secrets readable by the wrong eyes.

- **The browser never gets a token.** It gets a cookie that is encrypted and marked
  `httpOnly`, so JavaScript cannot read it — which means a script injected into the
  page has nothing to steal.
- The real token stays on the server.
- Cookies are marked `Secure` wherever the site runs on HTTPS.

## A03 · Injection

**The risk:** user input treated as code or as a query.

- All database access goes through SQLAlchemy with parameters. No SQL is built by
  joining strings.
- **Sorting uses a fixed list of allowed columns.** `?sort=id;DROP TABLE employee`
  is rejected as invalid input and never reaches the database.
- Editing a person works the same way: a fixed list of fields that may change.
- **The chatbot cannot write SQL.** It picks from a short list of functions and
  never sees the database schema, so a confused or manipulated model gives a wrong
  answer, not a data leak.

## A04 · Insecure design

**The risk:** the shape of the system is wrong, so no amount of patching helps.

- Login is handled in one place (the BFF), not repeated in each service.
- The salary service never receives a token. It receives **who you are, as headers**
  — and it only trusts those headers because nothing except the BFF can reach it.
- A **shared key** proves the caller is the BFF. If that key is not configured the
  service refuses everything, rather than quietly trusting anyone.
- A wrong key and a missing key give the same answer to the caller. Telling them
  apart would help an attacker work out what to fix.

## A05 · Misconfiguration

**The risk:** safe code, unsafe settings.

- Response headers stop the browser guessing file types, stop the app being put in
  an invisible frame, stop private pages being cached, and stop page addresses
  leaking to other sites.
- **Keycloak and the salary data use different databases and different logins.** The
  app's credentials cannot read the password tables, and vice versa.
- The salary service and the chat service publish no ports at all.

## A06 · Out-of-date components

**The risk:** a known hole in something we installed.

- Dependencies are pinned, so builds are repeatable.
- **Gap:** there is no automatic vulnerability scan in CI yet. This is the weakest
  item on the page, and it is an afternoon's work (`pip-audit`, `npm audit`).

## A07 · Login and session failures

**The risk:** someone signs in as somebody else.

- **This system never sees a password.** Keycloak does.
- Every token is fully checked: the signature against Keycloak's published keys,
  who issued it, who it was for, and whether it has expired.
- If a token fails, the error does not say *which* check failed — that would tell an
  attacker what to change.
- **Login attempts are rate limited**, so passwords cannot be guessed in bulk.
- **CSRF protection**, because cookies made it necessary. A browser sends cookies to
  any site that asks, which is why cookie logins can be abused by another website.
  The fix: the session holds a secret, a second cookie holds the same value, and
  every change must echo it back in a header. Another site can trigger the request
  but cannot read the value to include.

## A08 · Data integrity

**The risk:** data that is wrong, or half-written.

- An import is **all or nothing**. One bad row on line 7,431 means nothing is saved.
  Half an import is worse than none, because nobody can tell which half landed.
- File size and row count are checked **before** the file is read, not after.
- **A salary is never overwritten.** A change closes the old period and opens a new
  one, so past payroll stays answerable.
- The database itself guarantees one current salary per person. It is not left to
  the application to remember.

## A09 · Logging and monitoring

**The risk:** something happens and nobody can tell what, or who.

- **Every change writes an audit row** — what changed, from what, to what, by whom.
- Every log line carries a request id, so one request can be followed across
  services.
- **Questions and answers in the chatbot are never logged.** Both are free text
  about people's pay, and a log file is the wrong place for that.

## A10 · Server-side request forgery

**The risk:** making our server fetch something on an attacker's behalf.

- The BFF forwards only known paths to known services. A path it does not recognise
  is refused, not passed along.

---

## What is deliberately not production-ready

Local development runs on plain HTTP, so two settings are relaxed, and both must be
changed before this is deployed anywhere real:

| Setting | Local | Required in production |
|---|---|---|
| `cookie_secure` | `false` | `true` — cookies only over HTTPS |
| Keycloak `sslRequired` | `none` | `external` — with TLS in front |

The dev passwords and the gateway key in this repository are placeholders. They are
committed so a fresh clone starts, and they are not reused anywhere.
