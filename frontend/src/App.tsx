import { useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { AuthProvider } from "./hooks/useAuth";
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
import LearningAnalytics from "./pages/LearningAnalytics";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  const [chatOpen, setChatOpen] = useState(false);

  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN}>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route
                element={
                  <ProtectedRoute>
                    <AppLayout />
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
                </Route>
                <Route path="/assignments/:id" element={<AssignmentDetail />} />

                {/* Learning Analytics */}
                <Route path="/analytics" element={<LearningAnalytics />} />

                {/* Default redirect */}
                <Route path="/" element={<Navigate to="/assignments" replace />} />
                <Route path="*" element={<Navigate to="/assignments" replace />} />
              </Route>
            </Routes>
          </BrowserRouter>

          {/* Global AI Assistant */}
          <AIFloatButton onClick={() => setChatOpen(true)} />
          <ChatDrawer open={chatOpen} onClose={() => setChatOpen(false)} />
        </AuthProvider>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
