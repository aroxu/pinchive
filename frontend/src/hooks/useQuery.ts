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

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await fetcherRef.current();
      if (signal?.aborted) return;
      setState({ data, error: null, loading: false });
    } catch (err) {
      if (signal?.aborted) return;
      setState((prev) => ({
        data: prev.data,
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      }));
    }
  }, []);

  const refresh = useCallback(() => load(), [load]);

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    const controller = new AbortController();
    setState((prev) => ({ ...prev, loading: prev.data === null }));
    void load(controller.signal);

    if (!pollMs) return () => controller.abort();
    const timer = window.setInterval(() => void load(controller.signal), pollMs);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, pollMs, enabled, ...deps]);

  return { ...state, refresh, setData: (data: T) => setState({ data, error: null, loading: false }) };
}
