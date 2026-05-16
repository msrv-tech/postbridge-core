import { useState, useEffect, useCallback } from 'react'
import { isAuthenticated } from './adapters/sessionToken'
import { loadCurrentUser } from './adapters/auth'

export function useAuth() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(() => {
    if (!isAuthenticated()) {
      setUser(null);
      return Promise.resolve(null);
    }
    return loadCurrentUser()
      .then((data) => {
        setUser(data);
        return data;
      })
      .catch(() => {
        setUser(null);
        return null;
      });
  }, []);

  useEffect(() => {
    if (!isAuthenticated()) {
      setLoading(false);
      return;
    }
    refreshUser().finally(() => setLoading(false));
  }, [refreshUser]);

  return { user, loading, refreshUser };
}
