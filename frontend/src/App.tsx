import { Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth } from './auth/RequireAuth'
import { AppShell } from './layout/AppShell'
import { EmployeeDetailPage } from './pages/EmployeeDetailPage'
import { EmployeeListPage } from './pages/EmployeeListPage'
import { DashboardPage } from './pages/DashboardPage'
import { HealthPage } from './pages/HealthPage'
import { ImportPage } from './pages/ImportPage'
import { Providers } from './providers'

/** A page that is not built yet, so the shell can be used before they all are. */
function Placeholder({ title }: { title: string }) {
  return (
    <div className="u-page">
      <h1 className="u-title">{title}</h1>
      <p className="u-muted">Not built yet.</p>
    </div>
  )
}

export function App() {
  return (
    <Providers>
      <AuthProvider>
        <Routes>
          {/* Unauthenticated on purpose: a health page that needs a session cannot
              tell you the session service is down. Outside the shell, too - the
              shell needs a user to render. */}
          <Route path="/health" element={<HealthPage />} />

          <Route
            path="/*"
            element={
              <RequireAuth>
                <AppShell>
                  <Routes>
                    <Route path="/employees" element={<EmployeeListPage />} />
                    <Route path="/employees/:id" element={<EmployeeDetailPage />} />
                    <Route path="/dashboard" element={<DashboardPage />} />
                    <Route path="/import" element={<ImportPage />} />
                    <Route path="/fx-rates" element={<Placeholder title="FX Rates" />} />
                    {/* The employee list is the landing page: it is where the work
                        is, and where someone arrives wanting to change a salary. */}
                    <Route path="*" element={<Navigate to="/employees" replace />} />
                  </Routes>
                </AppShell>
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </Providers>
  )
}
