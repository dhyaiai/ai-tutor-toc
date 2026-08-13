import { useState, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import AppErrorBoundary from "./components/AppErrorBoundary";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import ProtectedRoute from "./components/ProtectedRoute";
import AppLayout from "./components/Layout/AppLayout";
import AssignmentLayout from "./components/Layout/AssignmentLayout";
import AIFloatButton from "./components/AIFloatButton";
import ChatDrawer from "./components/ChatDrawer";
import LoginPage from "./pages/Login";
import UploadAssignment from "./pages/AssignmentManagement/UploadAssignment";
import AssignmentRecords from "./pages/AssignmentManagement/AssignmentRecords";
import AssignmentDetail from "./pages/AssignmentManagement/AssignmentDetail";
import ErrorRedo from "./pages/AssignmentManagement/ErrorRedo";
import AIChallenge from "./pages/AssignmentManagement/AIChallenge";
import MyFavorites from "./pages/AssignmentManagement/MyFavorites";
import LearningAnalytics from "./pages/LearningAnalytics";
import OralAssessment from "./pages/OralAssessment";
import Composition from "./pages/Composition";
import PersonalityConfigPage from "./pages/Settings/PersonalityConfig";
import AccountManagement from "./pages/Settings/AccountManagement";
import DataDashboard from "./pages/Dashboard";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

/**
 * 全局 AI 助手宿主：只在受保护路由（登录后）渲染。
 * - 登出后随布局一起卸载，登录页不再出现聊天抽屉/悬浮按钮（隐私）；
 * - key 绑定 user.id：切换账号时强制重建抽屉，清空上一个账号的会话状态，
 *   防止新账号看到旧账号的聊天记录（跨账号数据泄漏）。
 */
function ChatHost({ children }: { children: ReactNode }) {
  const [chatOpen, setChatOpen] = useState(false);
  const { user } = useAuth();
  return (
    <>
      {children}
      <AIFloatButton onClick={() => setChatOpen(true)} />
      <ChatDrawer
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        key={user?.id ?? "anonymous"}
      />
    </>
  );
}

export default function App() {
  return (
    // 全局错误边界：渲染异常显示错误页而非白屏（见 AppErrorBoundary.tsx）
    <AppErrorBoundary>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN}>
        <AntApp>
          <AuthProvider queryClient={queryClient}>
            <BrowserRouter>
              <Routes>
                <Route path="/login" element={<LoginPage />} />
                <Route
                  element={
                    <ProtectedRoute>
                      <ChatHost>
                        <AppLayout />
                      </ChatHost>
                    </ProtectedRoute>
                  }
                >
                  {/* Assignment Management */}
                  <Route path="/assignments" element={<AssignmentLayout />}>
                    <Route index element={<Navigate to="records" replace />} />
                    <Route path="upload" element={<UploadAssignment />} />
                    <Route path="records" element={<AssignmentRecords />} />
                    <Route path="error-redo" element={<ErrorRedo />} />
                    <Route path="ai-challenge" element={<AIChallenge />} />
                    <Route path="favorites" element={<MyFavorites />} />
                  </Route>
                  <Route path="/assignments/:id" element={<AssignmentDetail />} />

                  {/* Learning Analytics */}
                  <Route path="/analytics" element={<LearningAnalytics />} />

                  {/* Oral Assessment */}
                  <Route path="/oral" element={<OralAssessment />} />

                  {/* Composition */}
                  <Route path="/composition" element={<Composition />} />

                  {/* Settings */}
                  <Route path="/settings/personality" element={<PersonalityConfigPage />} />
                  <Route path="/settings/account" element={<AccountManagement />} />

                  {/* Data Dashboard */}
                  <Route path="/dashboard" element={<DataDashboard />} />

                  {/* Default redirect */}
                  <Route path="/" element={<Navigate to="/assignments" replace />} />
                  <Route path="*" element={<Navigate to="/assignments" replace />} />
                </Route>
              </Routes>
            </BrowserRouter>
          </AuthProvider>
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
    </AppErrorBoundary>
  );
}
