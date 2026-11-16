// Create one client-agent fetch function with optional in-memory IAM handling.
export function createIamClient({ enabled, baseUrl, getSession, refresh, onExpired, fetchImpl = fetch }) {
  // Send a client-agent request with one optional refresh-and-retry on 401.
  return async function iamFetch(path, options = {}, retry = true) {
    const session = getSession();
    const headers = { ...(options.headers || {}) };
    if (enabled && session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
    const response = await fetchImpl(`${baseUrl}${path}`, { ...options, headers });
    if (enabled && response.status === 401 && retry) {
      const updated = await refresh();
      if (updated) return fetchImpl(`${baseUrl}${path}`, { ...options, headers: { ...headers, Authorization: `Bearer ${updated.access_token}` } });
      onExpired();
    }
    return response;
  };
}
