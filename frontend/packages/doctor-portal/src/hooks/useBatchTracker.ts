import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiClient } from '@hospital/shared';
import { ACTIVE_STATUSES } from '../types/batch';
import type { BatchSummary } from '../types/batch';

const POLL_MS = 5000;
export const ACTIVE_QUERY =
  `/reports/batches?${ACTIVE_STATUSES.map((s) => `status=${s}`).join('&')}&page_size=100`;

export function useBatchTracker(api: ApiClient, onSettled?: (b: BatchSummary) => void) {
  const [active, setActive] = useState<BatchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const timerRef = useRef<number | null>(null);
  const inFlightRef = useRef(false);
  const mountedRef = useRef(true);
  const activeRef = useRef<BatchSummary[]>([]);
  const onSettledRef = useRef(onSettled);
  onSettledRef.current = onSettled;

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const schedule = useCallback((fn: () => void) => {
    stop();
    timerRef.current = window.setTimeout(fn, POLL_MS);
  }, [stop]);

  const fetchActive = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const { data } = await api.get(ACTIVE_QUERY);
      if (!mountedRef.current) return;
      const items: BatchSummary[] = data.items || [];
      const prev = activeRef.current;
      const nextIds = new Set(items.map((b) => b.id));
      const settled = prev.filter((b) => !nextIds.has(b.id));
      activeRef.current = items;
      setActive(items);
      setError(false);
      setLoading(false);
      if (settled.length > 0) {
        void (async () => {
          const finals = await Promise.all(settled.map(async (b) => {
            try {
              const { data: d } = await api.get(`/reports/batches/${b.id}`);
              return (d.batch as BatchSummary) || b;
            } catch {
              return b;
            }
          }));
          if (mountedRef.current) {
            for (const f of finals) onSettledRef.current?.(f);
          }
        })();
      }
      if (items.length > 0) schedule(() => { void fetchActive(); });
    } catch {
      if (mountedRef.current) {
        setError(true);
        setLoading(false);
        if (activeRef.current.length > 0) schedule(() => { void fetchActive(); });
      }
    } finally {
      inFlightRef.current = false;
    }
  }, [api, schedule]);

  const wake = useCallback(() => {
    stop();
    void fetchActive();
  }, [fetchActive, stop]);

  useEffect(() => {
    mountedRef.current = true;
    void fetchActive();
    return () => {
      stop();
      mountedRef.current = false;
    };
  }, [fetchActive, stop]);

  return { active, loading, error, wake };
}
