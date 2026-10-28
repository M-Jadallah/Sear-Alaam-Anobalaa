import { useEffect, useState } from 'react';
import type { CurationBatch } from './pilot-types';

// This optional catalog is deliberately separate from required reader/search
// data. Older local APIs can return 404 without blocking their existing flows.
export function useCurationBatches() {
  const [state, setState] = useState<{ items: CurationBatch[]; loading: boolean }>({ items: [], loading: true });
  useEffect(() => {
    const controller = new AbortController();
    void fetch('/api/curation/batches', { signal: controller.signal })
      .then(async response => {
        if (!response.ok) return [];
        const result = await response.json();
        if (!Array.isArray(result.items)) return [];
        return result.items.filter((item: CurationBatch) => typeof item.id === 'string' && Array.isArray(item.biographies) && item.published !== false) as CurationBatch[];
      })
      .then(items => { if (!controller.signal.aborted) setState({ items, loading: false }); })
      .catch(() => { if (!controller.signal.aborted) setState({ items: [], loading: false }); });
    return () => controller.abort();
  }, []);
  return state;
}
