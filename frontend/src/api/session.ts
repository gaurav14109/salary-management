/**
 * One announcement: the session is gone.
 *
 * The identity call is not the only request that can come back 401. A session expires
 * while someone has the dashboard open, and the next thing they click gets refused -
 * a request the auth layer never made and would otherwise never hear about. Without
 * this, that page renders `Session expired` as an error message and offers the user
 * nothing to do about it.
 *
 * A tiny event bus rather than a direct import of the auth code, because the
 * dependency has to point this way: `client.ts` is the lowest layer in the app and
 * must not know that an AuthProvider exists. It states the fact; whoever cares
 * subscribes.
 */

export type SessionExpiredListener = (loginUrl: string) => void

/** Where to send someone when the server did not say. The BFF's own login route. */
export const DEFAULT_LOGIN_URL = '/auth/login'

const listeners = new Set<SessionExpiredListener>()

/** Subscribe. Returns the unsubscribe function, for an effect's cleanup. */
export function onSessionExpired(listener: SessionExpiredListener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * Announce that the session is no longer valid.
 *
 * Called from `client.ts` on a 401 and from nowhere else. Iterates a copy, so a
 * listener that unsubscribes itself while being called does not mutate the set
 * mid-iteration.
 */
export function notifySessionExpired(loginUrl: string = DEFAULT_LOGIN_URL): void {
  for (const listener of [...listeners]) listener(loginUrl)
}
