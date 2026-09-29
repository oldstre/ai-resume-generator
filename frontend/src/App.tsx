import { useEffect } from 'react'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router'
import { AppShell } from '@/components/AppShell'
import { useAuthStore } from '@/features/auth/store'
import AuthPage from '@/pages/AuthPage'
import CreatePage from '@/pages/CreatePage'
import ResumeDetailPage from '@/pages/ResumeDetailPage'
import ResumesPage from '@/pages/ResumesPage'
import { RequireAuth } from '@/routes/RequireAuth'

export default function App() {
  const restore = useAuthStore((state) => state.restore)

  useEffect(() => {
    void restore()
  }, [restore])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<AuthPage />} />

        <Route
          element={
            <RequireAuth>
              <AppShell>
                <Outlet />
              </AppShell>
            </RequireAuth>
          }
        >
          {/* 简历列表页（首页） */}
          <Route path="/resumes" element={<ResumesPage />} />
          {/* 简历详情页（动态参数 :id） */}
          <Route path="/resumes/:id" element={<ResumeDetailPage />} />
          {/* 创建简历页（表单填基本信息） */}
          <Route path="/create" element={<CreatePage />} />
        </Route>

        {/* fallback：所有未知路径都跳到简历列表 */}
        <Route path="*" element={<Navigate to="/resumes" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
