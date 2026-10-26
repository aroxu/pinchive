import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useQuery } from "./useQuery";

function deferred() {
  let resolve!: (value: string) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<string>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

type Request = ReturnType<typeof deferred>;
type Outcome = "resolve" | "reject";

function controlledFetcher() {
  const requests: Request[] = [];
  const fetcher = vi.fn((..._args: unknown[]) => {
    const request = deferred();
    requests.push(request);
    return request.promise;
  });
  return { fetcher, requests };
}

async function settle(request: Request, outcome: Outcome, value: string) {
  await act(async () => {
    if (outcome === "resolve") request.resolve(value);
    else request.reject(new Error(value));
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useQuery", () => {
  it.each([new Error("offline"), "offline"])(
    "keeps successful data visible during refresh and failure (%s)",
    async (error) => {
      const { fetcher, requests } = controlledFetcher();
      const { result } = renderHook(() => useQuery(fetcher));
      expect(result.current).toMatchObject({ data: null, error: null, loading: true });

      await settle(requests[0], "resolve", "cached");
      act(() => { void result.current.refresh(); });
      expect(result.current).toMatchObject({ data: "cached", error: null, loading: false });

      await act(async () => { requests[1].reject(error); });
      expect(result.current).toMatchObject({ data: "cached", error: "offline", loading: false });

      act(() => { void result.current.refresh(); });
      expect(result.current.data).toBe("cached");
      await settle(requests[2], "resolve", "recovered");
      expect(result.current).toMatchObject({ data: "recovered", error: null, loading: false });
    },
  );

  it("keeps polling data progressing when every response takes multiple poll intervals", async () => {
    vi.useFakeTimers();
    let request = 0;
    const fetcher = vi.fn(() => {
      const data = `response ${++request}`;
      return new Promise<string>((resolve) => {
        window.setTimeout(() => resolve(data), 2500);
      });
    });
    const { result } = renderHook(() => useQuery(fetcher, [], { pollMs: 1000 }));

    for (let response = 1; response <= 3; response++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(response === 1 ? 2500 : 3000);
      });
      expect(result.current).toMatchObject({ data: `response ${response}`, error: null, loading: false });
      expect(fetcher).toHaveBeenCalledTimes(response);
    }
  });

  it.each<Outcome>(["resolve", "reject"])(
    "ignores older requests that finish with %s after manual refresh and polling",
    async (outcome) => {
      vi.useFakeTimers();
      const { fetcher, requests } = controlledFetcher();
      const { result } = renderHook(() => useQuery(fetcher, [], { pollMs: 1000 }));
      await settle(requests[0], "resolve", "cached");
      act(() => { vi.advanceTimersByTime(1000); });
      act(() => { void result.current.refresh(); });
      act(() => { void result.current.refresh(); });
      expect(fetcher).toHaveBeenCalledTimes(4);

      await settle(requests[3], "resolve", "newest manual");
      // Superseded requests still pending must not block the next poll.
      act(() => { vi.advanceTimersByTime(1000); });
      expect(fetcher).toHaveBeenCalledTimes(5);
      await settle(requests[4], "resolve", "newest");
      await settle(requests[1], outcome, "old poll");
      await settle(requests[2], outcome, "old manual");
      expect(result.current).toMatchObject({ data: "newest", error: null, loading: false });
    },
  );

  it("keeps loading until the newest request settles, then retains its error", async () => {
    const { fetcher, requests } = controlledFetcher();
    const { result } = renderHook(() => useQuery(fetcher));
    act(() => { void result.current.refresh(); });
    act(() => { void result.current.refresh(); });

    await settle(requests[0], "resolve", "old");
    expect(result.current).toMatchObject({ data: null, error: null, loading: true });
    await settle(requests[2], "reject", "newest failure");
    await settle(requests[1], "resolve", "still old");
    expect(result.current).toMatchObject({ data: null, error: "newest failure", loading: false });
  });

  it.each<Outcome>(["resolve", "reject"])(
    "allows manual refresh to supersede a poll without stale %s unlocking polling",
    async (outcome) => {
      vi.useFakeTimers();
      const { fetcher, requests } = controlledFetcher();
      const { result } = renderHook(() => useQuery(fetcher, [], { pollMs: 1000 }));
      await settle(requests[0], "resolve", "cached");

      act(() => { vi.advanceTimersByTime(1000); });
      act(() => { void result.current.refresh(); });
      await settle(requests[1], outcome, "stale poll");
      act(() => { vi.advanceTimersByTime(2000); });
      expect(fetcher).toHaveBeenCalledTimes(3);
      expect(result.current).toMatchObject({ data: "cached", error: null, loading: false });

      await settle(requests[2], "resolve", "newest");
      expect(result.current).toMatchObject({ data: "newest", error: null, loading: false });
    },
  );

  it.each<Outcome>(["resolve", "reject"])(
    "waits for manual refresh to %s before automatic polling resumes",
    async (outcome) => {
      vi.useFakeTimers();
      const { fetcher, requests } = controlledFetcher();
      const { result } = renderHook(() => useQuery(fetcher, [], { pollMs: 1000 }));
      await settle(requests[0], "resolve", "cached");
      act(() => { void result.current.refresh(); });
      act(() => { vi.advanceTimersByTime(3000); });
      expect(fetcher).toHaveBeenCalledTimes(2);

      await settle(requests[1], outcome, "manual result");
      expect(result.current).toMatchObject({
        data: outcome === "resolve" ? "manual result" : "cached",
        error: outcome === "reject" ? "manual result" : null,
        loading: false,
      });
      act(() => { vi.advanceTimersByTime(1000); });
      expect(fetcher).toHaveBeenCalledTimes(3);
      await settle(requests[2], "resolve", "polled result");
      expect(result.current).toMatchObject({ data: "polled result", error: null, loading: false });
    },
  );

  it("keeps refresh stable and awaitable while using the latest inline fetcher", async () => {
    const { fetcher, requests } = controlledFetcher();
    const { result, rerender } = renderHook(
      ({ value }) => useQuery(() => fetcher(value)),
      { initialProps: { value: "first" } },
    );
    const refresh = result.current.refresh;
    await settle(requests[0], "resolve", "cached");
    rerender({ value: "second" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.refresh).toBe(refresh);

    let completion!: Promise<void>;
    act(() => { completion = refresh(); });
    expect(fetcher).toHaveBeenLastCalledWith("second");
    expect(result.current).toMatchObject({ data: "cached", error: null, loading: false });
    await settle(requests[1], "resolve", "refreshed");
    await expect(completion).resolves.toBeUndefined();
    expect(result.current.data).toBe("refreshed");
  });

  it.each<Outcome>(["resolve", "reject"])(
    "invalidates automatic and manual requests on dependency changes (%s)",
    async (outcome) => {
      const { fetcher, requests } = controlledFetcher();
      const { result, rerender } = renderHook(
        ({ board }) => useQuery(() => fetcher(board), [board]),
        { initialProps: { board: 1 } },
      );
      act(() => { void result.current.refresh(); });
      rerender({ board: 2 });
      expect(fetcher).toHaveBeenLastCalledWith(2);
      await settle(requests[0], outcome, "old automatic");
      await settle(requests[1], outcome, "old manual");
      expect(result.current).toMatchObject({ data: null, error: null, loading: true });

      await settle(requests[2], "resolve", "board 2");
      act(() => { void result.current.refresh(); });
      rerender({ board: 1 });
      expect(result.current).toMatchObject({ data: "board 2", error: null, loading: false });
      await settle(requests[4], "resolve", "board 1");
      await settle(requests[3], outcome, "stale board 2");
      expect(result.current).toMatchObject({ data: "board 1", error: null, loading: false });
    },
  );

  it("does not fetch or poll while initially disabled, then loads when enabled", async () => {
    vi.useFakeTimers();
    const { fetcher, requests } = controlledFetcher();
    const { result, rerender } = renderHook(
      ({ enabled }) => useQuery(fetcher, [], { enabled, pollMs: 1000 }),
      { initialProps: { enabled: false } },
    );
    expect(result.current).toMatchObject({ data: null, error: null, loading: false });
    let completion!: Promise<void>;
    act(() => { completion = result.current.refresh(); });
    expect(fetcher).not.toHaveBeenCalled();
    await expect(completion).resolves.toBeUndefined();
    act(() => { vi.advanceTimersByTime(3000); });
    expect(fetcher).not.toHaveBeenCalled();

    rerender({ enabled: true });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.loading).toBe(true);
    await settle(requests[0], "resolve", "enabled");
    expect(result.current).toMatchObject({ data: "enabled", error: null, loading: false });
  });

  it.each<Outcome>(["resolve", "reject"])(
    "clears data on disable and ignores pending work even after re-enabling (%s)",
    async (outcome) => {
      vi.useFakeTimers();
      const { fetcher, requests } = controlledFetcher();
      const { result, rerender } = renderHook(
        ({ enabled }) => useQuery(fetcher, [], { enabled, pollMs: 1000 }),
        { initialProps: { enabled: true } },
      );
      const refresh = result.current.refresh;
      await settle(requests[0], "resolve", "cached");
      act(() => { vi.advanceTimersByTime(1000); });
      act(() => { void refresh(); });
      rerender({ enabled: false });
      expect(result.current).toMatchObject({ data: null, error: null, loading: false });
      expect(vi.getTimerCount()).toBe(0);
      let completion!: Promise<void>;
      act(() => { completion = refresh(); });
      expect(fetcher).toHaveBeenCalledTimes(3);
      await expect(completion).resolves.toBeUndefined();
      act(() => { vi.advanceTimersByTime(3000); });
      expect(fetcher).toHaveBeenCalledTimes(3);
      await settle(requests[2], outcome, "disabled manual");
      expect(result.current).toMatchObject({ data: null, error: null, loading: false });

      rerender({ enabled: true });
      await settle(requests[3], "resolve", "re-enabled");
      await settle(requests[1], outcome, "previous poll");
      expect(result.current).toMatchObject({ data: "re-enabled", error: null, loading: false });
    },
  );

  it("invalidates in-flight work and clears the interval when polling changes", async () => {
    vi.useFakeTimers();
    const { fetcher, requests } = controlledFetcher();
    const { result, rerender } = renderHook(
      ({ pollMs }) => useQuery(fetcher, [], { pollMs }),
      { initialProps: { pollMs: 1000 } },
    );
    act(() => { void result.current.refresh(); });
    rerender({ pollMs: 0 });
    expect(vi.getTimerCount()).toBe(0);
    await settle(requests[2], "resolve", "current");
    await settle(requests[0], "resolve", "old automatic");
    await settle(requests[1], "resolve", "old manual");
    act(() => { vi.advanceTimersByTime(3000); });
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.current).toMatchObject({ data: "current", error: null, loading: false });
  });

  it.each<Outcome>(["resolve", "reject"])(
    "stops polling and makes a retained refresh harmless after unmount (%s)",
    async (outcome) => {
      vi.useFakeTimers();
      const { fetcher, requests } = controlledFetcher();
      const { result, unmount } = renderHook(() => useQuery(fetcher, [], { pollMs: 1000 }));
      const refresh = result.current.refresh;
      let completion!: Promise<void>;
      act(() => { completion = refresh(); });
      unmount();
      expect(vi.getTimerCount()).toBe(0);
      act(() => { vi.advanceTimersByTime(3000); });
      let afterUnmount!: Promise<void>;
      act(() => { afterUnmount = refresh(); });
      expect(fetcher).toHaveBeenCalledTimes(2);
      await expect(afterUnmount).resolves.toBeUndefined();
      await settle(requests[0], outcome, "unmounted automatic");
      await settle(requests[1], outcome, "unmounted manual");
      await expect(completion).resolves.toBeUndefined();
    },
  );

  it("ignores requests from StrictMode's discarded effect setup", async () => {
    vi.useFakeTimers();
    const { fetcher, requests } = controlledFetcher();
    const { result } = renderHook(() => useQuery(fetcher, [], { pollMs: 1000 }), { reactStrictMode: true });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(1);
    await settle(requests[1], "resolve", "current setup");
    await settle(requests[0], "resolve", "discarded setup");
    expect(result.current).toMatchObject({ data: "current setup", error: null, loading: false });

    act(() => { result.current.setData("saved locally"); });
    expect(result.current).toMatchObject({ data: "saved locally", error: null, loading: false });
  });
});
