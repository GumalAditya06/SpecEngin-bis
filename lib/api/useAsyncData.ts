"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type AsyncData<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  retry: () => void;
};

/**
 * Shared client-side data-loading primitive.
 *
 * Centralizes the loading / error / retry states so pages don't each
 * reimplement them with scattered fetch() calls. The fetcher identity is
 * captured per-mount (like useEffect's default dependency semantics) —
 * callers that need to change parameters should remount via `key` or drive
 * refetches through retry(), which keeps the semantics simple.
 */
export function useAsyncData<T>(fetcher: () => Promise<T>): AsyncData<T> {
  // The fetcher may be an inline closure that changes every render; keep the
  // latest one in a ref (synced in an effect, never during render) so the
  // fetch effect only re-runs on mount / retry.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    // All state updates happen after the await so the effect body itself
    // never sets state synchronously (react-hooks/set-state-in-effect).
    (async () => {
      try {
        const result = await fetcherRef.current();
        if (cancelled) return;
        setData(result);
        setError(null);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Something went wrong.");
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // Re-run only on mount and when retry() bumps `attempt`.
  }, [attempt]);

  const retry = useCallback(() => {
    setLoading(true);
    setError(null);
    setAttempt((n) => n + 1);
  }, []);

  return { data, loading, error, retry };
}
