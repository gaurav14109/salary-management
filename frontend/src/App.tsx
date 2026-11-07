import { Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth } from './auth/RequireAuth'
import { HealthPage } from './pages/HealthPage'
import { HomePage } from './pages/HomePage'

export function App() {
  return (
    <AuthProvider>
      <main>
        <h1>Employee Salary Management</h1>
        <Routes>
          {/* Unauthenticated on purpose: a health page that needs a session cannot
              tell you the session service is down. */}
          <Route path="/health" element={<HealthPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <HomePage />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </AuthProvider>
  )
}
