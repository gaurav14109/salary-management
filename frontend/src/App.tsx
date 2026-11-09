import { Navigate, Route, Routes } from 'react-router-dom'

import { AuthProvider } from './auth/AuthProvider'
import { RequireAuth } from './auth/RequireAuth'
import { RequireRole } from './auth/RequireRole'
import { AppShell } from './layout/AppShell'
import { EmployeeDetailPage } from './pages/EmployeeDetailPage'
import { EmployeeListPage } from './pages/EmployeeListPage'
import { DashboardPage } from './pages/DashboardPage'
import { HealthPage } from './pages/HealthPage'
import { ImportPage } from './pages/ImportPage'
import { Providers } from './providers'

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
                    {/* Importing writes employees and salaries, so it is the HR
                        manager's. An admin reads the organisation and changes none
                        of it - without this, typing the URL renders a dropzone
                        whose every upload fails at 403. */}
                    <Route
                      path="/import"
                      element={
                        <RequireRole role="hr_manager">
                          <ImportPage />
                        </RequireRole>
                      }
                    />
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
