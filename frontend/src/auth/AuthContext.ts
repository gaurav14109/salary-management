import { createContext } from 'react'

export type User = {
  username: string
  email: string | null
  roles: string[]
}

export type AuthState =
  | { status: 'checking' }
  | { status: 'authenticated'; user: User }
  | { status: 'anonymous'; loginUrl: string }
  | { status: 'unreachable'; message: string }

export type AuthContextValue = AuthState & {
  login: () => void
  logout: () => Promise<void>
  hasRole: (role: string) => boolean
}

/** Split from the provider so each file exports one kind of thing. */
export const AuthContext = createContext<AuthContextValue | null>(null)
