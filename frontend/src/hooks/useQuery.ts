import { useCallback, useEffect, useRef, useState } from "react";

interface State<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Fetch-on-mount with optional polling and manual refresh.
 *
 * Polling replaces the HTMX partial refresh the server-rendered UI used: a live
 * download updates counters and pins without a reload. Re-fetches keep the last
 * successful payload visible so the grid never flashes empty mid-poll.
 */
export function useQuery<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
  options: { pollMs?: number; enabled?: boolean } = {},
) {
  const { pollMs, enabled = true } = options;
  const [state, setState] = useState<State<T>>({
    data: null,
    error: null,
    loading: enabled,
  });

  // Keep the latest fetcher without making it a dependency: an inline arrow
  // would otherwise restart the poll on every render.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  // Automatic and manual requests share one lifetime and ordering guard.
  const activeRef = useRef(false);
  const requestRef = useRef(0);
  const inFlightRef = useRef(false);

  const load = useCallback(async (automatic = false) => {
    // A slow request must be allowed to finish between automatic polls.
    // Manual refresh can still supersede the current request.
    if (!activeRef.current || (automatic && inFlightRef.current)) return;
    const request = ++requestRef.current;
    inFlightRef.current = true;
    try {
      const data = await fetcherRef.current();
      if (!activeRef.current || request !== requestRef.current) return;
      setState({ data, error: null, loading: false });
    } catch (err) {
      if (!activeRef.current || request !== requestRef.current) return;
      setState((prev) => ({
        data: prev.data,
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      }));
    } finally {
      if (request === requestRef.current) inFlightRef.current = false;
    }
  }, []);

  const refresh = useCallback(() => load(), [load]);

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    activeRef.current = true;
    setState((prev) => ({ ...prev, loading: prev.data === null }));
    void load();

    const timer = pollMs ? window.setInterval(() => void load(true), pollMs) : undefined;
    return () => {
      activeRef.current = false;
      // A subsequent setup (including StrictMode's replay) must not revive
      // requests started by the previous effect, even manual refreshes.
      ++requestRef.current;
      inFlightRef.current = false;
      if (timer !== undefined) window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, pollMs, enabled, ...deps]);

  return { ...state, refresh, setData: (data: T) => setState({ data, error: null, loading: false }) };
}
