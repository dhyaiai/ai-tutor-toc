import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import { authService, type TokenResponse } from "../services/authService";

interface AuthState {
  user: { id: number; username: string } | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, email?: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<{ id: number; username: string } | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Initialize from stored tokens
  useEffect(() => {
    const accessToken = localStorage.getItem("access_token");
    if (accessToken) {
      // Try to restore session - just trust the token for now
      // A failed request will trigger the 401 interceptor to clear state
      setUser({ id: 0, username: "" });
    }
    setIsLoading(false);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result: TokenResponse = await authService.login({ username, password });
    localStorage.setItem("access_token", result.access_token);
    localStorage.setItem("refresh_token", result.refresh_token);
    setUser({ id: result.user_id, username: result.username });
  }, []);

  const register = useCallback(async (username: string, password: string, email?: string) => {
    await authService.register({ username, password, email });
  }, []);

  const logout = useCallback(() => {
    authService.logout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
