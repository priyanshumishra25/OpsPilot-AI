import { Navigate, Route, Routes } from "react-router";
import { Layout } from "./components/Layout";
import { useAuth } from "./lib/auth";
import { ActivityPage } from "./pages/ActivityPage";
import { AskPage } from "./pages/AskPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { LoginPage } from "./pages/LoginPage";
import { OverviewPage } from "./pages/OverviewPage";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { session } = useAuth();
  return session ? children : <Navigate to="/login" replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<OverviewPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
