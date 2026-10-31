import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthGate, AuthProvider } from "./components/Auth";
import { BackendProvider } from "./components/BackendStatus";
import { Shell } from "./components/Shell";
import { AutoEditPage } from "./pages/AutoEditPage";
import { HomePage } from "./pages/HomePage";
import { MyBrollPage } from "./pages/MyBrollPage";
import { SessionPage } from "./pages/SessionPage";

export function StudioRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<HomePage />} />
        <Route path="auto-edit" element={<AutoEditPage />} />
        <Route path="my-broll" element={<MyBrollPage />} />
        <Route path="sessions/:sessionId" element={<SessionPage />} />
        <Route path="*" element={<HomePage />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <BackendProvider>
        <AuthProvider>
          <AuthGate>
            <StudioRoutes />
          </AuthGate>
        </AuthProvider>
      </BackendProvider>
    </BrowserRouter>
  );
}
