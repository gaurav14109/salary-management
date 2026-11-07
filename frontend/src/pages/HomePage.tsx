import { useAuth } from '../auth/useAuth'

/** Placeholder landing page. Replaced by the employee list in Slice 3. */
export function HomePage() {
  const auth = useAuth()
  if (auth.status !== 'authenticated') return null

  return (
    <section>
      <p>
        Signed in as <strong>{auth.user.username}</strong>
        {auth.user.email ? ` (${auth.user.email})` : null}
      </p>
      <p>Roles: {auth.user.roles.join(', ') || 'none'}</p>
      <button onClick={() => void auth.logout()}>Sign out</button>
    </section>
  )
}
