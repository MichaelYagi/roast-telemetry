import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api/client.js";

const AuthContext = createContext(null);

// Wraps the whole app (see App.jsx) -- one /auth/me call on mount decides
// whether to render the login screen or the real app. A 401 here is the
// expected "not logged in" response, not a failure -- everything else
// (network error, 500) is treated the same way (fall back to logged-out)
// since there's nothing more useful to show either way.
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    return api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function logout() {
    await api.logout().catch(() => {});
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, refresh, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
